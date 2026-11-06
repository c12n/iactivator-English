// iActivator Mockup: a design and flow mockup of the desktop iActivator app.
//
// IMPORTANT: this app is a SIMULATION. A real iActivator has to talk to a second
// iPhone over USB (usbmux / lockdown), which iOS does not let any app do, so the
// working program is the desktop app for Windows and macOS in this repository.
// Everything here that concerns a "connected iPhone" is sample data. The one real
// panel is "This iPhone", which reads this device's own public information.

import SwiftUI
import UIKit

@main
struct IActivatorMockupApp: App {
    @StateObject private var model = MockModel()
    var body: some Scene {
        WindowGroup {
            ContentView().environmentObject(model)
        }
    }
}

// MARK: - Shared state

enum StepStatus { case pending, running, done, warn }

struct SimStep: Identifiable {
    let id = UUID()
    let name: String
    var status: StepStatus = .pending
    var detail: String = ""
}

struct CheckRow: Identifiable {
    let id = UUID()
    let name: String
    let status: String   // ok | warn | bad | info
    let detail: String
}

final class MockModel: ObservableObject {
    @Published var steps: [SimStep] = []
    @Published var log: [String] = [
        "iActivator Mockup started. This is a simulation: no device is connected or changed.",
    ]
    @Published var checks: [CheckRow] = []
    @Published var running = false

    func addLog(_ line: String) { log.append(line) }

    @MainActor
    func runActivation(language: String, region: String, wifi: String) async {
        guard !running else { return }
        running = true
        checks = []
        var names = ["Activation", "Language & region", "Skip setup screens"]
        if !wifi.isEmpty { names.append("Wi-Fi") }
        steps = names.map { SimStep(name: $0) }
        addLog("▶ Activation (simulated): \(language), \(region)" + (wifi.isEmpty ? "" : ", Wi-Fi \"\(wifi)\""))
        for i in steps.indices {
            steps[i].status = .running
            try? await Task.sleep(for: .seconds(1.1))
            steps[i].status = .done
            addLog("  \(steps[i].name): done (simulated)")
        }
        addLog("✅ Activation (simulated): done. Nothing was applied to any device.")
        running = false
    }

    @MainActor
    func runDiagnostics() async {
        guard !running else { return }
        running = true
        addLog("Diagnostics (simulated): checking…")
        try? await Task.sleep(for: .seconds(1.4))
        checks = [
            CheckRow(name: "Find My (Activation Lock)", status: "ok", detail: "No lock (sample data)"),
            CheckRow(name: "MDM (remote management)", status: "ok", detail: "Not managed (sample data)"),
            CheckRow(name: "Battery health", status: "ok", detail: "92% (5120 of 5570 mAh): healthy (sample data)"),
            CheckRow(name: "Charge cycles", status: "ok", detail: "214 (sample data)"),
            CheckRow(name: "Wi-Fi module", status: "ok", detail: "Address present (sample data)"),
            CheckRow(name: "Modem", status: "ok", detail: "Firmware present, IMEI readable (sample data)"),
            CheckRow(name: "Storage", status: "warn", detail: "9.4 of 128 GB free (7%): sample data"),
            CheckRow(name: "Restarts (panic)", status: "ok", detail: "No crashes in the last 30 days (sample data)"),
        ]
        addLog("Diagnostics (simulated): no warnings except storage. Nothing was read from a real device.")
        running = false
    }
}

// MARK: - Root

struct ContentView: View {
    var body: some View {
        VStack(spacing: 0) {
            SimulationBanner()
            TabView {
                ActivationView()
                    .tabItem { Label("Activation", systemImage: "bolt.fill") }
                WiFiView()
                    .tabItem { Label("Wi-Fi", systemImage: "wifi") }
                DiagnosticsView()
                    .tabItem { Label("Diagnostics", systemImage: "waveform.path.ecg") }
                HistoryView()
                    .tabItem { Label("History", systemImage: "clock.arrow.circlepath") }
                MoreView()
                    .tabItem { Label("More", systemImage: "ellipsis.circle") }
            }
        }
    }
}

struct SimulationBanner: View {
    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text("SIMULATION: sample data only. Nothing is connected or changed.")
                .font(.footnote.weight(.semibold))
        }
        .foregroundStyle(.white)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 7)
        .background(Color.orange)
    }
}

func statusColor(_ s: String) -> Color {
    switch s { case "ok": return .green; case "warn": return .orange; case "bad": return .red; default: return .gray }
}

func stepIcon(_ s: StepStatus) -> (String, Color) {
    switch s {
    case .done: return ("checkmark.circle.fill", .green)
    case .running: return ("arrow.triangle.2.circlepath", .blue)
    case .warn: return ("exclamationmark.triangle.fill", .orange)
    case .pending: return ("circle", .gray)
    }
}

// MARK: - Activation

struct ActivationView: View {
    @EnvironmentObject var model: MockModel
    @State private var language = "English (US)"
    @State private var region = "United States"
    @State private var timezone = "America/New_York"
    @State private var use24h = true
    @State private var activate = true
    @State private var skipSetup = true
    @State private var supervise = false
    @State private var ssid = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Label("Sample iPhone 15 Pro · iOS 18 · Not activated", systemImage: "iphone")
                        .font(.subheadline)
                    Text("Sample data. The desktop app reads the real device over USB.")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Text("Device") }

                Section {
                    Picker("Language", selection: $language) {
                        ForEach(["English (US)", "English (UK)", "Ukrainian", "German", "French", "Spanish"], id: \.self) { Text($0) }
                    }
                    Picker("Region", selection: $region) {
                        ForEach(["United States", "Canada", "United Kingdom", "Ukraine", "Germany", "France"], id: \.self) { Text($0) }
                    }
                    HStack { Text("Time zone"); Spacer(); Text(timezone).foregroundStyle(.secondary) }
                    Toggle("24-hour format", isOn: $use24h)
                } header: { Text("Language & region") }

                Section {
                    Toggle("Activate", isOn: $activate)
                    Toggle("Skip the setup screens", isOn: $skipSetup)
                    Toggle("Supervised mode", isOn: $supervise)
                    TextField("Wi-Fi network (optional)", text: $ssid)
                } header: { Text("Mode") }

                if !model.steps.isEmpty {
                    Section {
                        ForEach(model.steps) { step in
                            let (icon, color) = stepIcon(step.status)
                            HStack { Image(systemName: icon).foregroundStyle(color); Text(step.name) }
                        }
                    } header: { Text("Progress (simulated)") }
                }

                Section {
                    Button {
                        Task { await model.runActivation(language: language, region: region, wifi: ssid) }
                    } label: {
                        HStack { Image(systemName: "play.fill"); Text("Run (simulated)") }
                            .frame(maxWidth: .infinity)
                    }
                    .disabled(model.running)
                }
            }
            .navigationTitle("Activation")
        }
    }
}

// MARK: - Wi-Fi

struct WiFiView: View {
    @EnvironmentObject var model: MockModel
    @State private var ssid = ""
    @State private var password = ""
    @State private var security = "Auto (WPA2/WPA3)"
    @State private var hidden = false
    @State private var noPrivateAddress = true
    @State private var note: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Network (SSID)", text: $ssid)
                    SecureField("Password", text: $password)
                    Picker("Security", selection: $security) {
                        ForEach(["Auto (WPA2/WPA3)", "WPA/WPA2", "WPA2", "WPA3", "WEP", "No password"], id: \.self) { Text($0) }
                    }
                    Toggle("Hidden network", isOn: $hidden)
                    Toggle("No private address", isOn: $noPrivateAddress)
                } header: { Text("Network") } footer: {
                    Text("In the desktop app, passwords are stored in the system keychain and the profile is verified on the device after installation.")
                }
                if let note {
                    Section { Label(note, systemImage: "info.circle").font(.footnote) }
                }
                Section {
                    Button {
                        note = ssid.isEmpty
                            ? "Enter a network name first (in the real app this is checked before anything is sent)."
                            : "Simulated: the profile for \"\(ssid)\" would now be installed by the desktop app and read back from the iPhone to confirm."
                        model.addLog("Wi-Fi only (simulated): \(ssid.isEmpty ? "no network given" : ssid)")
                    } label: {
                        HStack { Image(systemName: "wifi"); Text("Install Wi-Fi only (simulated)") }
                            .frame(maxWidth: .infinity)
                    }
                }
                ThisIPhoneSection()
            }
            .navigationTitle("Wi-Fi")
        }
    }
}

// MARK: - Diagnostics

struct DiagnosticsView: View {
    @EnvironmentObject var model: MockModel
    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button {
                        Task { await model.runDiagnostics() }
                    } label: {
                        HStack { Image(systemName: "waveform.path.ecg"); Text("Run check (simulated)") }
                    }
                    .disabled(model.running)
                    if model.checks.isEmpty {
                        Text("Battery, charging, Wi-Fi, Bluetooth, modem, storage, crash logs from the last 30 days, Find My and MDM. In the desktop app the iPhone must be unlocked.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
                if !model.checks.isEmpty {
                    Section {
                        ForEach(model.checks) { row in
                            HStack(alignment: .top, spacing: 10) {
                                Circle().fill(statusColor(row.status)).frame(width: 9, height: 9).padding(.top, 5)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(row.name).fontWeight(.semibold)
                                    Text(row.detail).font(.footnote).foregroundStyle(.secondary)
                                }
                            }
                        }
                    } header: { Text("Results (sample data)") }
                }
                Section {
                    Label("Erase iPhone is disabled in the mockup, on purpose.", systemImage: "trash.slash")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Text("Danger zone") }
            }
            .navigationTitle("Diagnostics")
        }
    }
}

// MARK: - History

struct HistoryView: View {
    private let rows: [(String, String, String)] = [
        ("iPhone 15 Pro · 256 GB", "Activation", "Today, 10:42 · Success (sample)"),
        ("iPhone 12 · 128 GB", "Diagnostics", "Yesterday, 16:03 · Warnings (sample)"),
        ("iPhone SE (3rd generation) · 64 GB", "Activation", "Nov 3, 09:15 · Success (sample)"),
    ]
    var body: some View {
        NavigationStack {
            List {
                Section {
                    HStack {
                        StatBox(value: "3", label: "Total devices")
                        StatBox(value: "1", label: "Today")
                        StatBox(value: "2", label: "Activated")
                        StatBox(value: "1", label: "With warnings")
                    }
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                }
                Section {
                    ForEach(rows, id: \.0) { row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.0).fontWeight(.semibold)
                            Text("\(row.1) · \(row.2)").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                } footer: {
                    Text("Sample history. The desktop app keeps a real history and can open it as a spreadsheet file.")
                }
            }
            .navigationTitle("History")
        }
    }
}

struct StatBox: View {
    let value: String, label: String
    var body: some View {
        VStack(spacing: 2) {
            Text(value).font(.title3.weight(.bold))
            Text(label).font(.caption2).foregroundStyle(.secondary).multilineTextAlignment(.center)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 8)
    }
}

// MARK: - More: real own-device panel, log, about

struct ThisIPhoneSection: View {
    var body: some View {
        Section {
            InfoRow("Name", UIDevice.current.name)
            InfoRow("Model", Self.modelIdentifier())
            InfoRow("iOS", UIDevice.current.systemVersion)
            InfoRow("Battery", Self.batteryText())
            InfoRow("Language", Locale.current.identifier)
            InfoRow("Region", Locale.current.region?.identifier ?? "—")
        } header: { Text("This iPhone (real)") } footer: {
            Text("Read from this device itself with public iOS APIs. This is the only real data in the mockup.")
        }
    }
    static func modelIdentifier() -> String {
        var info = utsname()
        uname(&info)
        return withUnsafePointer(to: &info.machine) {
            $0.withMemoryRebound(to: CChar.self, capacity: 1) { String(validatingUTF8: $0) ?? "iPhone" }
        }
    }
    static func batteryText() -> String {
        UIDevice.current.isBatteryMonitoringEnabled = true
        let level = UIDevice.current.batteryLevel
        return level >= 0 ? "\(Int(level * 100))%" : "—"
    }
}

struct InfoRow: View {
    let key: String, value: String
    init(_ key: String, _ value: String) { self.key = key; self.value = value }
    var body: some View {
        HStack { Text(key); Spacer(); Text(value).foregroundStyle(.secondary) }
    }
}

struct MoreView: View {
    @EnvironmentObject var model: MockModel
    var body: some View {
        NavigationStack {
            List {
                ThisIPhoneSection()
                Section {
                    ForEach(Array(model.log.suffix(12).enumerated()), id: \.offset) { _, line in
                        Text(line).font(.footnote.monospaced())
                    }
                } header: { Text("Log (this session)") }
                Section {
                    InfoRow("Version", "1.0 (mockup)")
                    InfoRow("Real app", "iActivator 2.7.1 for Windows & macOS")
                    Text("Why a mockup? A real iActivator talks to a second iPhone over USB using usbmux and lockdown services. iOS gives apps no way to do that with another iPhone, not in the App Store and not sideloaded, so the working program is the desktop app in this repository.")
                        .font(.footnote).foregroundStyle(.secondary)
                    Text("Original project by mr1garik. English edition and this mockup by c12n Apps.")
                        .font(.footnote).foregroundStyle(.secondary)
                } header: { Text("About") }
            }
            .navigationTitle("More")
        }
    }
}
