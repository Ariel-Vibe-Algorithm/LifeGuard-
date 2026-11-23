from PyInstaller.utils.hooks import collect_submodules, collect_data_files
hidden=[]
for package in ("speech_recognition","sounddevice","cv2","PIL","tkintermapview","pyttsx3","gtts","serial","pycaw","comtypes"):
    try: hidden += collect_submodules(package)
    except Exception: pass
datas=[("content", "content")]  # הנחיות החירום (נדרש - בלעדיו מרכז הבטיחות ריק)
# עזרה ראשונה אופליין: המדריכים, המקורות ואוצר המילים לחיפוש (בלעדיהם המסך מציג רק 101)
datas += [("first_aid/content", "first_aid/content"), ("first_aid/synonyms.json", "first_aid"),
          ("first_aid/supplement_policy.json", "first_aid")]   # content/ כולל supplements/ (חבילות הרחבה)
datas += [("safe_places/safe_places_il.json", "safe_places")]   # מקומות בטוחים מובנים (עובד בלי אינטרנט)
try: datas += collect_data_files("tkintermapview")
except Exception: pass
a=Analysis(["main.py"], pathex=[], binaries=[], datas=datas, hiddenimports=hidden,
           hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=["pytest","unittest"], noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name="LifeGuard",debug=False,
        bootloader_ignore_signals=False,strip=False,upx=True,console=False,
        disable_windowed_traceback=False)
