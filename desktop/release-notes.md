Biggest River desktop app {TAG}. The app carries its own Python, so you do not have to install anything else.

## Download

- **Mac with Apple silicon (M1 and later):** `Biggest-River-{VERSION}-arm64.dmg`
- **Mac with an Intel chip:** `Biggest-River-{VERSION}-x64.dmg`
- **Windows (x64, and Windows on Arm):** `Biggest-River-{VERSION}-x64-setup.exe`

To find your Mac's chip, open the Apple menu and choose **About This Mac**.

## Open the app on a Mac

Apple has not verified the app yet, because it is not notarized. The first time you open it, macOS shows this message: "Apple could not verify 'Biggest River' is free of malware". To open the app:

1. Open the `.dmg` file, then drag **Biggest River** to **Applications**.
2. Open **Biggest River** from Applications. When the message appears, click **Done**. Do not click **Move to Trash**.
3. Open **System Settings**, then **Privacy & Security**.
4. Scroll down to **Security**. At the line "Biggest River was blocked", click **Open Anyway**.
5. Enter your Mac password, then click **Open Anyway** again.

On macOS 14 and older: right-click the app in Applications, choose **Open**, then click **Open**.

macOS checks the app before each start while the app has its download mark. The app icon can bounce in the Dock for a minute or two before the window opens. To make the app open at once, remove the download mark. Open **Terminal** and enter this command:

```
xattr -dr com.apple.quarantine "/Applications/Biggest River.app"
```

This command removes the macOS download check for this app only. Do it only for an app that you downloaded from this page.

If macOS says that the app "is damaged", you have an older copy. Move that app to the Trash and download this release.

## Open the app on Windows

The installer is not signed yet, so Windows SmartScreen shows "Windows protected your PC". To continue:

1. Click **More info**.
2. Click **Run anyway**.
