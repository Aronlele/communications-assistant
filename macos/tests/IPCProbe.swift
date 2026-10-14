import Foundation
import Darwin

@main
struct IPCProbe {
    static func main() throws {
        var descriptors: [Int32] = [0, 0]
        guard socketpair(AF_UNIX, SOCK_STREAM, 0, &descriptors) == 0 else { exit(2) }
        let reader = FileHandle(fileDescriptor: descriptors[0], closeOnDealloc: true)
        let writer = FileHandle(fileDescriptor: descriptors[1], closeOnDealloc: true)
        let link = EngineLink()
        let first = DispatchSemaphore(value: 0)
        let second = DispatchSemaphore(value: 0)
        link.onObject = { object in
            if object.string("event") == "ready" { first.signal() }
            if object.string("event") == "确认" { second.signal() }
        }
        DispatchQueue.global().async { link.read(reader) }
        try writer.write(contentsOf: Data("{\"event\":\"ready\"}\n".utf8))
        guard first.wait(timeout: .now() + 2) == .success else {
            writer.closeFile()
            print("FAIL: a small complete frame was buffered instead of delivered immediately")
            exit(1)
        }
        let fragmented = Data("{\"event\":\"确认\"}\n".utf8)
        try writer.write(contentsOf: fragmented.prefix(12))
        try writer.write(contentsOf: fragmented.dropFirst(12))
        guard second.wait(timeout: .now() + 2) == .success else {
            writer.closeFile()
            print("FAIL: fragmented UTF-8 frame was not delivered")
            exit(1)
        }
        writer.closeFile()
        print("Native IPC: immediate small frames and fragmented UTF-8 passed")
    }
}
