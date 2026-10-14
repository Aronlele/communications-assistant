import AppKit

let output = URL(fileURLWithPath: CommandLine.arguments[1])
try FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
let image = NSImage(size: NSSize(width: 1024, height: 1024))
image.lockFocus()
let background = NSBezierPath(roundedRect: NSRect(x: 50, y: 50, width: 924, height: 924), xRadius: 208, yRadius: 208)
NSGradient(starting: NSColor(red: 0.06, green: 0.37, blue: 0.57, alpha: 1), ending: NSColor(red: 0.12, green: 0.66, blue: 0.68, alpha: 1))!.draw(in: background, angle: 65)
let plane = NSBezierPath()
plane.move(to: NSPoint(x: 234, y: 505)); plane.line(to: NSPoint(x: 790, y: 768))
plane.line(to: NSPoint(x: 606, y: 257)); plane.line(to: NSPoint(x: 485, y: 407))
plane.line(to: NSPoint(x: 637, y: 617)); plane.line(to: NSPoint(x: 420, y: 456)); plane.close()
NSColor.white.setFill(); plane.fill()
let tail = NSBezierPath()
tail.move(to: NSPoint(x: 452, y: 395)); tail.line(to: NSPoint(x: 463, y: 277)); tail.line(to: NSPoint(x: 513, y: 348)); tail.close()
NSColor.white.withAlphaComponent(0.72).setFill(); tail.fill()
image.unlockFocus()
for size in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let pixels = size * scale
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        image.draw(in: NSRect(x: 0, y: 0, width: pixels, height: pixels))
        NSGraphicsContext.restoreGraphicsState()
        let name = "icon_\(size)x\(size)\(scale == 2 ? "@2x" : "").png"
        try bitmap.representation(using: .png, properties: [:])!.write(to: output.appendingPathComponent(name))
    }
}
