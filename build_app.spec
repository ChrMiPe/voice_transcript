block_cipher = None

a = Analysis(
    ["src/voice_transcript/menubar.py"],
    pathex=["src"],
    binaries=[],
    datas=[("assets", "assets")],
    hiddenimports=[
        "voice_transcript",
        "voice_transcript.menubar",
        "voice_transcript.main",
        "voice_transcript.llm",
        "voice_transcript.cleanup",
        "voice_transcript.shortcuts",
        "voice_transcript.notify",
        "voice_transcript.config",
        "voice_transcript.hotkey",
        "voice_transcript.llm_server",
        "rumps",
        "AppKit",
        "Cocoa",
        "Foundation",
        "objc",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PIL", "scipy", "pandas",
        "mlx", "mlx_lm", "transformers", "tokenizers", "torch",
        "safetensors", "sentencepiece", "huggingface_hub",
    ],
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Voice Transcript",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Voice Transcript",
)

app = BUNDLE(
    coll,
    name="Voice Transcript.app",
    icon="icon.icns",
    bundle_identifier="com.voicetranscript.app",
    info_plist={
        "CFBundleName": "Voice Transcript",
        "CFBundleDisplayName": "Voice Transcript",
        "CFBundleVersion": "0.1.0",
        "CFBundleShortVersionString": "0.1.0",
        "LSUIElement": True,
        "NSMicrophoneUsageDescription": "Voice Transcript benoetigt Mikrofon-Zugriff fuer Spracherkennung.",
    },
)
