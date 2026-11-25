// sysaudio: records what this Mac plays (its system audio) into a WAV file.
//
// Built on ScreenCaptureKit (macOS 13+), so it hears every app on any output
// device, headphones included, without touching the Mac's sound settings.
// It needs the Screen & System Audio Recording permission, which macOS gives
// to the app this tool was started from (for example Terminal).
//
//   sysaudio check      prints OK or NO: is the permission granted?
//   sysaudio request    asks macOS for the permission (system prompt), prints OK or NO
//   sysaudio record OUT.wav [--max-seconds N]          (N defaults to 720)
//       prints READY once capturing, LEVEL <rms dBFS> about every 0.5 s, and
//       DONE <seconds> after stopping. Stops on SIGINT/SIGTERM/SIGHUP, on a
//       "stop" line or end of input on stdin, or after N seconds.
//       Exit codes: 0 done; 2 with PERMISSION when not allowed; 1 with ERROR <message>.
//
// The WAV is 16-bit PCM, 48 kHz, stereo. Its length follows the wall clock from
// READY to the stop: if macOS leaves out stretches of silence, they are written
// back as silence, so timing and rhythm stay true.
//
// For tests, `record ... --test-tone HZ` replaces the capture with a quiet
// synthetic tone (no permission needed, nothing is played); see startTone().
// Set SYSAUDIO_DEBUG=1 for notes on stderr.

import AVFoundation
import CoreGraphics
import CoreMedia
import Foundation
import ScreenCaptureKit

let sampleRate = 48_000.0
let channels: AVAudioChannelCount = 2
let defaultMaxSeconds = 720.0
let longestMaxSeconds = 24 * 3600.0
let gapSeconds = 0.05 // a jump in timestamps longer than this is missing audio
let debugMode = ProcessInfo.processInfo.environment["SYSAUDIO_DEBUG"] != nil

// MARK: - Output

let outputLock = NSLock()

func say(_ line: String) {
    outputLock.lock()
    fputs(line + "\n", stdout)
    fflush(stdout)
    outputLock.unlock()
}

func note(_ message: @autoclosure () -> String) {
    if debugMode { fputs("sysaudio: \(message())\n", stderr) }
}

func fail(_ message: String) -> Never {
    say("ERROR " + message.replacingOccurrences(of: "\n", with: " "))
    exit(1)
}

func denied() -> Never {
    say("PERMISSION")
    exit(2)
}

func isPermissionError(_ error: Error) -> Bool {
    let e = error as NSError
    if e.domain == SCStreamErrorDomain && e.code == SCStreamError.Code.userDeclined.rawValue { return true }
    return !CGPreflightScreenCaptureAccess()
}

func hostNow() -> CMTime { CMClockGetTime(CMClockGetHostTimeClock()) }

func seconds(from start: CMTime, to end: CMTime) -> Double {
    CMTimeGetSeconds(CMTimeSubtract(end, start))
}

// MARK: - Audio buffers

/// Copies the PCM samples of a captured CMSampleBuffer into an AVAudioPCMBuffer.
func pcmBuffer(from sampleBuffer: CMSampleBuffer) -> AVAudioPCMBuffer? {
    guard CMSampleBufferIsValid(sampleBuffer),
          let description = CMSampleBufferGetFormatDescription(sampleBuffer) else { return nil }
    let frames = CMSampleBufferGetNumSamples(sampleBuffer)
    let format = AVAudioFormat(cmAudioFormatDescription: description)
    guard frames > 0,
          let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else { return nil }
    buffer.frameLength = AVAudioFrameCount(frames)
    let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
        sampleBuffer, at: 0, frameCount: Int32(frames), into: buffer.mutableAudioBufferList)
    if status != noErr {
        note("could not copy audio samples (\(status))")
        return nil
    }
    return buffer
}

/// Wraps PCM samples in a CMSampleBuffer stamped `pts`, the way ScreenCaptureKit delivers them.
func makeSampleBuffer(_ buffer: AVAudioPCMBuffer, pts: CMTime) -> CMSampleBuffer? {
    var timing = CMSampleTimingInfo(
        duration: CMTime(value: 1, timescale: CMTimeScale(buffer.format.sampleRate)),
        presentationTimeStamp: pts, decodeTimeStamp: .invalid)
    var sampleBuffer: CMSampleBuffer?
    guard CMSampleBufferCreate(
        allocator: kCFAllocatorDefault, dataBuffer: nil, dataReady: false,
        makeDataReadyCallback: nil, refcon: nil, formatDescription: buffer.format.formatDescription,
        sampleCount: CMItemCount(buffer.frameLength), sampleTimingEntryCount: 1,
        sampleTimingArray: &timing, sampleSizeEntryCount: 0, sampleSizeArray: nil,
        sampleBufferOut: &sampleBuffer) == noErr, let sampleBuffer else { return nil }
    guard CMSampleBufferSetDataBufferFromAudioBufferList(
        sampleBuffer, blockBufferAllocator: kCFAllocatorDefault,
        blockBufferMemoryAllocator: kCFAllocatorDefault, flags: 0,
        bufferList: buffer.audioBufferList) == noErr else { return nil }
    return sampleBuffer
}

// MARK: - Recorder

final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate {
    /// Everything below is touched only on this queue (audio, timers, signals, stop).
    let queue = DispatchQueue(label: "sysaudio.audio", autoreleaseFrequency: .workItem)
    private let screenQueue = DispatchQueue(label: "sysaudio.screen", autoreleaseFrequency: .workItem)

    private let url: URL
    private let maxFrames: AVAudioFramePosition
    private let testToneHz: Double?

    private var file: AVAudioFile?
    private var stream: SCStream?
    private var converter: AVAudioConverter?
    private var levelTimer: DispatchSourceTimer?
    private var toneTimer: DispatchSourceTimer?
    private var started = false
    private var finished = false
    private var startTime = CMTime.invalid // host time of READY
    private var lastEnd = CMTime.invalid   // host time where the previous buffer ended
    private var written: AVAudioFramePosition = 0
    private var levelSum = 0.0
    private var levelCount = 0

    init(url: URL, maxSeconds: Double, testToneHz: Double?) {
        self.url = url
        self.maxFrames = AVAudioFramePosition((maxSeconds * sampleRate).rounded())
        self.testToneHz = testToneHz
    }

    // MARK: Starting

    func begin() {
        if testToneHz != nil {
            queue.async {
                guard !self.finished else { return }
                self.openFile()
                self.captureStarted(error: nil)
            }
            return
        }
        SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, error in
            self.queue.async { self.startStream(content: content, error: error) }
        }
    }

    private func openFile() {
        let settings: [String: Any] = [
            AVAudioFileTypeKey: kAudioFileWAVEType,
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: Int(channels),
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        do {
            // Samples go in as 32-bit float and are stored as 16-bit integers.
            file = try AVAudioFile(forWriting: url, settings: settings,
                                   commonFormat: .pcmFormatFloat32, interleaved: false)
        } catch {
            fail("cannot write \(url.path): \(error.localizedDescription)")
        }
    }

    private func startStream(content: SCShareableContent?, error: Error?) {
        guard !finished else { return }
        guard let content else {
            if let error, isPermissionError(error) { denied() }
            fail("cannot list displays: \(error?.localizedDescription ?? "no answer from ScreenCaptureKit")")
        }
        let mainID = CGMainDisplayID()
        guard let display = content.displays.first(where: { $0.displayID == mainID }) ?? content.displays.first else {
            fail("no display to capture")
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])

        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.excludesCurrentProcessAudio = true
        config.sampleRate = Int(sampleRate)
        config.channelCount = Int(channels)
        // Video cannot be turned off, so ask for as little as possible: 2x2 pixels, at most once a second.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        config.showsCursor = false

        openFile()
        let stream = SCStream(filter: filter, configuration: config, delegate: self)
        do {
            try stream.addStreamOutput(self, type: .audio, sampleHandlerQueue: queue)
            try stream.addStreamOutput(self, type: .screen, sampleHandlerQueue: screenQueue)
        } catch {
            discardFile()
            fail("cannot set up capture: \(error.localizedDescription)")
        }
        self.stream = stream
        stream.startCapture { error in
            self.queue.async { self.captureStarted(error: error) }
        }
    }

    private func captureStarted(error: Error?) {
        guard !finished else { return }
        if let error {
            discardFile()
            if isPermissionError(error) { denied() }
            fail("cannot start capture: \(error.localizedDescription)")
        }
        started = true
        startTime = hostNow()
        say("READY")

        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now() + 0.5, repeating: 0.5)
        timer.setEventHandler { [unowned self] in self.reportLevel() }
        timer.resume()
        levelTimer = timer

        queue.asyncAfter(deadline: .now() + Double(maxFrames) / sampleRate) { self.stop() }
        if let hz = testToneHz { startTone(hz) }
    }

    // MARK: Capturing

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        if type == .audio { handleAudio(sampleBuffer) } // video frames are dropped
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        queue.async {
            guard !self.finished else { return }
            let e = error as NSError
            if e.domain == SCStreamErrorDomain && e.code == SCStreamError.Code.userStopped.rawValue {
                self.stop() // stopped from the system's menu: keep what was recorded
                return
            }
            self.finished = true
            self.levelTimer?.cancel()
            self.closeFile()
            if isPermissionError(error) { denied() }
            fail("capture stopped: \(error.localizedDescription)")
        }
    }

    private func handleAudio(_ sampleBuffer: CMSampleBuffer) {
        guard started, !finished,
              let input = pcmBuffer(from: sampleBuffer),
              let buffer = toFileFormat(input) else { return }
        fillGap(before: sampleBuffer, frames: input.frameLength, rate: input.format.sampleRate)
        measure(buffer)
        write(buffer)
        if written >= maxFrames { stop() }
    }

    /// macOS can skip delivering audio while nothing plays; write that time back as silence.
    private func fillGap(before sampleBuffer: CMSampleBuffer, frames: AVAudioFrameCount, rate: Double) {
        let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuffer)
        guard pts.isNumeric else { return }
        let gap = seconds(from: lastEnd.isValid ? lastEnd : startTime, to: pts)
        // Timestamps are host time; a gap can never be longer than the recording so far.
        if gap > gapSeconds && gap < seconds(from: startTime, to: hostNow()) + 1 {
            note(String(format: "filling %.3f s of missing audio", gap))
            writeSilence(AVAudioFramePosition((gap * sampleRate).rounded()))
        }
        lastEnd = CMTimeAdd(pts, CMTime(value: CMTimeValue(frames), timescale: CMTimeScale(rate)))
    }

    private func toFileFormat(_ input: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let target = file?.processingFormat else { return nil }
        let source = input.format
        if source.sampleRate == target.sampleRate && source.channelCount == target.channelCount
            && source.commonFormat == target.commonFormat && source.isInterleaved == target.isInterleaved {
            return input
        }
        // Not what was asked for (48 kHz stereo float): convert.
        if converter?.inputFormat != source {
            note("converting from \(source)")
            converter = AVAudioConverter(from: source, to: target)
        }
        guard let converter else { return nil }
        let capacity = AVAudioFrameCount((Double(input.frameLength) * target.sampleRate / source.sampleRate)
            .rounded(.up)) + 64
        guard let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { return nil }
        var handedOver = false
        var error: NSError?
        let status = converter.convert(to: output, error: &error) { _, inputStatus in
            if handedOver {
                inputStatus.pointee = .noDataNow
                return nil
            }
            handedOver = true
            inputStatus.pointee = .haveData
            return input
        }
        if status == .error {
            note("conversion failed: \(error?.localizedDescription ?? "unknown")")
            return nil
        }
        return output
    }

    // MARK: Level

    private func measure(_ buffer: AVAudioPCMBuffer) {
        guard let data = buffer.floatChannelData else { return }
        let frames = Int(buffer.frameLength)
        let count = Int(buffer.format.channelCount)
        var sum = 0.0
        for channel in 0..<count {
            let samples = data[channel]
            for i in 0..<frames {
                let v = Double(samples[i])
                sum += v * v
            }
        }
        levelSum += sum
        levelCount += frames * count
    }

    private func reportLevel() {
        guard !finished else { return }
        var db = -120.0
        if levelCount > 0 && levelSum > 0 {
            db = max(-120.0, 10 * log10(levelSum / Double(levelCount)))
        }
        levelSum = 0
        levelCount = 0
        say(String(format: "LEVEL %.1f", db))
    }

    // MARK: Writing

    /// Appends to the WAV, never past the limit.
    private func write(_ buffer: AVAudioPCMBuffer) {
        guard let file, buffer.frameLength > 0 else { return }
        let room = maxFrames - written
        guard room > 0 else { return }
        if AVAudioFramePosition(buffer.frameLength) > room { buffer.frameLength = AVAudioFrameCount(room) }
        do {
            try file.write(from: buffer)
        } catch {
            finished = true
            levelTimer?.cancel()
            closeFile()
            fail("cannot write \(url.path): \(error.localizedDescription)")
        }
        written += AVAudioFramePosition(buffer.frameLength)
    }

    private func writeSilence(_ frames: AVAudioFramePosition) {
        guard let format = file?.processingFormat else { return }
        let chunk: AVAudioFrameCount = 4800
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunk),
              let data = buffer.floatChannelData else { return }
        for channel in 0..<Int(format.channelCount) {
            data[channel].initialize(repeating: 0, count: Int(chunk))
        }
        var left = min(frames, maxFrames - written)
        while left > 0 {
            let n = AVAudioFrameCount(min(left, AVAudioFramePosition(chunk)))
            buffer.frameLength = n
            write(buffer)
            left -= AVAudioFramePosition(n)
        }
    }

    private func closeFile() {
        // Releasing the AVAudioFile closes it, which writes the final sizes into the WAV header.
        autoreleasepool { file = nil }
    }

    private func discardFile() {
        closeFile()
        try? FileManager.default.removeItem(at: url)
    }

    // MARK: Stopping

    /// Ends the recording: fills in silence up to now, closes the WAV, prints DONE, exits 0.
    func stop() {
        guard !finished else { return }
        if startTime.isValid {
            let elapsed = seconds(from: startTime, to: hostNow())
            let target = min(maxFrames, AVAudioFramePosition((elapsed * sampleRate).rounded()))
            if target > written { writeSilence(target - written) }
        }
        finished = true
        levelTimer?.cancel()
        toneTimer?.cancel()
        closeFile()
        say(String(format: "DONE %.1f", Double(written) / sampleRate))
        guard let stream else { exit(0) }
        // Let ScreenCaptureKit wind down, but never wait on it for long.
        queue.asyncAfter(deadline: .now() + 2) { exit(0) }
        stream.stopCapture { _ in exit(0) }
    }

    // MARK: Test source

    /// Stands in for ScreenCaptureKit (`--test-tone HZ`): a quiet sine (amplitude 0.1) in
    /// 1024-frame buffers stamped with host time and paced like real capture, with no
    /// buffers at all from 0.5 s to 0.8 s, the way macOS can leave out silence.
    private func startTone(_ hz: Double) {
        guard let format = AVAudioFormat(standardFormatWithSampleRate: sampleRate, channels: channels) else { return }
        let chunk: AVAudioFrameCount = 1024
        var made: Int64 = 0
        let timer = DispatchSource.makeTimerSource(queue: queue)
        timer.schedule(deadline: .now(), repeating: Double(chunk) / sampleRate)
        timer.setEventHandler { [unowned self] in
            let due = Int64(seconds(from: self.startTime, to: hostNow()) * sampleRate)
            while !self.finished && made + Int64(chunk) <= due {
                let first = made
                made += Int64(chunk)
                let t = Double(first) / sampleRate
                if t >= 0.5 && t < 0.8 { continue }
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: chunk),
                      let data = buffer.floatChannelData else { return }
                buffer.frameLength = chunk
                for channel in 0..<Int(channels) {
                    for i in 0..<Int(chunk) {
                        data[channel][i] = Float(0.1 * sin(2 * Double.pi * hz * Double(first + Int64(i)) / sampleRate))
                    }
                }
                let pts = CMTimeAdd(self.startTime, CMTime(value: first, timescale: CMTimeScale(sampleRate)))
                if let sampleBuffer = makeSampleBuffer(buffer, pts: pts) { self.handleAudio(sampleBuffer) }
            }
        }
        timer.resume()
        toneTimer = timer
    }
}

// MARK: - Main

setvbuf(stdout, nil, _IOLBF, 0)
let arguments = Array(CommandLine.arguments.dropFirst())
let usage = "usage: sysaudio check | request | record OUT.wav [--max-seconds N]"

switch arguments.first ?? "" {
case "check":
    say(CGPreflightScreenCaptureAccess() ? "OK" : "NO")
    exit(0)
case "request":
    say(CGRequestScreenCaptureAccess() ? "OK" : "NO")
    exit(0)
case "record":
    break
default:
    fail(usage)
}

func positiveNumber(_ flag: String, _ value: String?) -> Double {
    guard let value, let number = Double(value), number.isFinite, number > 0 else {
        fail("\(flag) needs a positive number")
    }
    return number
}

var outPath: String?
var maxSeconds = defaultMaxSeconds
var testToneHz: Double?
var index = 1
while index < arguments.count {
    let arg = arguments[index]
    let next = index + 1 < arguments.count ? arguments[index + 1] : nil
    index += 1
    switch arg {
    case "--max-seconds":
        maxSeconds = positiveNumber(arg, next)
        index += 1
    case "--test-tone":
        testToneHz = positiveNumber(arg, next)
        index += 1
    default:
        if outPath == nil && !arg.hasPrefix("-") { outPath = arg } else { fail(usage) }
    }
}
guard let path = outPath else { fail(usage) }
if maxSeconds > longestMaxSeconds { fail("--max-seconds is at most \(Int(longestMaxSeconds))") }

if testToneHz == nil {
    if !CGPreflightScreenCaptureAccess() { denied() }
    // Connect to the window server first; ScreenCaptureKit expects it in a command-line tool.
    _ = CGMainDisplayID()
}

let recorder = Recorder(url: URL(fileURLWithPath: path), maxSeconds: maxSeconds, testToneHz: testToneHz)

// Signals stop the recording cleanly (the WAV is finished first) instead of killing the process.
signal(SIGPIPE, SIG_IGN) // a closed stdout must not kill us before the file is finished
let signalSources: [DispatchSourceSignal] = [SIGINT, SIGTERM, SIGHUP].map { number in
    signal(number, SIG_IGN)
    let source = DispatchSource.makeSignalSource(signal: number, queue: recorder.queue)
    source.setEventHandler { recorder.stop() }
    source.resume()
    return source
}

// A "stop" line, or the end of stdin, also stops.
Thread {
    while let line = readLine() {
        if line.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() == "stop" { break }
    }
    recorder.queue.async { recorder.stop() }
}.start()

recorder.begin()

// Keep the main run loop (which also serves the main dispatch queue) alive until stop() exits.
withExtendedLifetime(signalSources) {
    RunLoop.main.add(Timer(timeInterval: 86_400, repeats: true) { _ in }, forMode: .default)
    RunLoop.main.run()
}
