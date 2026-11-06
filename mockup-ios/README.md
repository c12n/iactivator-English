# iActivator Mockup for iOS

A design and flow mockup of iActivator, as an iPhone app: the full interface (Activation, Wi-Fi, Diagnostics, History, Log), an animated simulated run, and one real panel, "This iPhone", which reads this device's own name, model, iOS version, battery, language and region with public iOS APIs.

**It is a simulation, and it says so on screen.** Sample device data is labelled as sample data everywhere it appears, the erase action is deliberately inert, and a permanent banner at the top reads: "SIMULATION: sample data only. Nothing is connected or changed."

## Why a mockup, not the real thing

A real iActivator talks to a *second* iPhone over USB: a host usbmux connection to lockdownd, then the mobile activation, mobile configuration (cloud configuration, profiles, erase), diagnostics and crash report services. iOS gives apps none of that host stack for another iPhone:

| Route | Why it does not work |
|---|---|
| Public USB / accessory APIs | No API lets an app enumerate or claim a cabled iPhone's usbmux interface; phone-to-phone cable behaviour is charging |
| External Accessory / MFi | For MFi accessories speaking their own protocols; a stock iPhone is not an MFi accessory |
| DriverKit | iPad only (M1 and later, iPadOS 16), entitlement-gated, scoped to a vendor's own peripherals |
| Sideloading | Changes distribution, not capability; existing sideloaded tools talk lockdown only to the *same* device, through a computer-made pairing file |
| Apple's own answer | Apple Configurator for iPhone only adds devices to Apple Business wirelessly; setting up an iPhone by cable takes Configurator for **Mac** |

An Android version is a different story: possible in principle (public USB host APIs, and the libimobiledevice stack has run on Android in privileged contexts), but no finished unrooted APK performing lockdown activation was found. It is research-grade, not a port. See the full assessment in the repository history of this project, and the About tab inside the mockup.

## Build

Unsigned sideload IPA, built by the `build-mockup` GitHub Actions workflow (XcodeGen project, `project.yml`). The workflow publishes a **pre-release** tagged `mockup-v1.0`, so it never replaces the desktop app's latest release. Install with Feather or your usual signing tool.

Bundle ID: `com.c12n.iactivator.mockup` | Version 1.0 | iOS 17 and later
