// Draws the iActivator app icon (1024x1024 PNG) on a macOS runner.
// Usage: swift scripts/make-icon.swift <output-path>
// Same design as tools/make_icons.py: light rounded tile, dark phone, bolt, cable.
import AppKit

let outPath = CommandLine.arguments.count > 1 ? CommandLine.arguments[1]
    : "Sources/Assets.xcassets/AppIcon.appiconset/icon-1024.png"
let size: CGFloat = 1024
let k = size / 84

func color(_ hex: UInt32) -> NSColor {
    NSColor(red: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
}
let bg = color(0xF6F6F3), edge = color(0xE2E2DC), ink = color(0x2E3035), cable = color(0x9AA3AD)

let image = NSImage(size: NSSize(width: size, height: size))
image.lockFocus()
guard let ctx = NSGraphicsContext.current else { exit(1) }
ctx.shouldAntialias = true
// Work in the logo's top-left coordinate space (84 units).
let flip = NSAffineTransform()
flip.translateX(by: 0, yBy: size)
flip.scaleX(by: 1, yBy: -1)
flip.concat()

func rect(_ x: CGFloat, _ y: CGFloat, _ w: CGFloat, _ h: CGFloat, _ r: CGFloat, fill: NSColor, stroke: NSColor? = nil) {
    let p = NSBezierPath(roundedRect: NSRect(x: x * k, y: y * k, width: w * k, height: h * k),
                         xRadius: r * k, yRadius: r * k)
    fill.setFill(); p.fill()
    if let stroke { stroke.setStroke(); p.lineWidth = k; p.stroke() }
}

rect(0.5, 0.5, 83, 83, 20, fill: bg, stroke: edge)
rect(27, 10, 30, 50, 8, fill: ink)
rect(31, 15, 22, 38, 4, fill: bg)

let bolt = NSBezierPath()
let pts: [(CGFloat, CGFloat)] = [(38, 30), (42, 24), (42, 32), (46, 32), (42, 40), (42, 34)]
bolt.move(to: NSPoint(x: pts[0].0 * k, y: pts[0].1 * k))
for pt in pts.dropFirst() { bolt.line(to: NSPoint(x: pt.0 * k, y: pt.1 * k)) }
bolt.close()
ink.setFill(); bolt.fill()

let line = NSBezierPath()
line.move(to: NSPoint(x: 42 * k, y: 60 * k))
line.line(to: NSPoint(x: 42 * k, y: 74 * k))
line.lineWidth = 4 * k
line.lineCapStyle = .round
cable.setStroke(); line.stroke()

image.unlockFocus()

guard let tiff = image.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    FileHandle.standardError.write("icon render failed\n".data(using: .utf8)!)
    exit(1)
}
try! png.write(to: URL(fileURLWithPath: outPath))
print("wrote \(outPath)")
