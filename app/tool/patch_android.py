"""Adds what the generated Android project is missing: the app's name and the permissions video calls need.

Run from the app folder after `flutter create`:  python3 tool/patch_android.py
(The Android project is generated during the build and not stored in the repository yet.)
"""
import re
from pathlib import Path

MANIFEST = Path("android/app/src/main/AndroidManifest.xml")
PERMISSIONS = """
    <uses-feature android:name="android.hardware.camera" android:required="false" />
    <uses-feature android:name="android.hardware.camera.autofocus" android:required="false" />
    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.CHANGE_NETWORK_STATE" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.BLUETOOTH" android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.BLUETOOTH_ADMIN" android:maxSdkVersion="30" />
    <uses-permission android:name="android.permission.BLUETOOTH_CONNECT" />
"""

text = MANIFEST.read_text()
if "android.permission.CAMERA" not in text:
    text, count = re.subn(r"(<manifest[^>]*>)", lambda m: m.group(1) + PERMISSIONS, text, count=1)
    assert count == 1, "could not find the <manifest> tag"
text, count = re.subn(r'android:label="[^"]*"', 'android:label="10 Takar Doctor"', text, count=1)
assert count == 1, "could not find the app label"
MANIFEST.write_text(text)
print("Android manifest patched.")
