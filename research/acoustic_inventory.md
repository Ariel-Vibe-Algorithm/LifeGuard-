# LifeGuard: סקירת מודלים, דאטהסטים ומתודולוגיה לזיהוי אקוסטי של סכנה

- **תאריך גישה לכל המקורות:** 2026-11-05 (אלא אם צוין אחרת).
- **הקשר:** מחקר אקדמי לא מסחרי.
- **שיטה:** כל עובדה כאן נלקחה ממקור שנפתח בפועל, דרך WebFetch, curl או הורדת קובץ. עובדה שלא אומתה מסומנת **"not verified"**.
- **הסתייגות:** חלק מהמקורות נקראו דרך WebFetch, שמסכם את הדף באמצעות מודל קטן. נתונים קריטיים (רשימת המחלקות, נוסח החוק, טבלת Whisper, גודלי הקבצים, הרישיונות ב-Kaggle) אומתו גם מול הקובץ הגולמי עצמו, כמצוין בכל סעיף.

---

## 0. תקציר מנהלים

- **מודל מומלץ ל-v1:**
  - **YAMNet**, בגרסת TFLite הרשמית של Google (Kaggle Models). הרישיון Apache 2.0, אומת ב-Kaggle API.
  - יש שתי גרסאות:
    - `classification-tflite`: מכומתת, 4,126,810 בתים. הקלט 15,600 דגימות ב-16 kHz, הפלט 521 ציונים.
    - `tflite`: float, 16,096,668 בתים. הקלט באורך משתנה, והפלט כולל scores, embeddings (1024) ו-log-mel.
  - שתיהן הורדו ונטענו בהצלחה ב-Windows עם `ai-edge-litert` 2.3.0, בלי TensorFlow מלא.
  - ההשהיה שנמדדה: כ-3–5 ms לחלון של 0.975 s במעבד של המחשב.
- **אסטרטגיה:**
  - בשלב הראשון משתמשים בציוני 521 המחלקות של YAMNet כ-zero-shot.
  - בשלב השני מאמנים ראש קטן (logistic regression או MLP) על ה-embeddings של YAMNet, עם דאטה ייעודי של סכנה ועם hard negatives.
  - מבחן אמיתי שהרצתי מראה ש-zero-shot לבדו חלש: קטע בכי תינוק מ-ESC-50 סווג כ-Speech/Laughter. ראו סעיף 7.
- **דאטהסטים מובילים:**
  - FSD50K: CC-BY ברמת הדאטהסט, ולכל קטע רישיון משלו (CC0, CC-BY, CC-BY-NC, Sampling+).
  - ESC-50: CC BY-NC 3.0.
  - UrbanSound8K: CC BY-NC 4.0.
  - DCASE2017 Task 2: "Other (Non-Commercial)".
  - MIVIA Audio Events: דרך טופס בקשה, הרישיון not verified.
  - AudioSet: התוויות ב-CC BY 4.0, והאודיו נשאר ביוטיוב, כך שחלק מהקטעים כבר אינם זמינים.
- **עברית:** Whisper-tiny חלש מאוד בעברית: WER 71.6% ב-FLEURS לפי טבלה 13 במאמר. הנתיב המומלץ ל-KWS על המכשיר:
  - (א) מסווג מילות מפתח קטן על Google speech_embedding (Apache 2.0), שמאומן על הקלטות עצמיות ועל דגימות TTS עבריות (Piper he_IL).
  - (ב) לחלופין, ASR עברי streaming של ivrit.ai: `zipformer2-66m-streaming-he` ב-sherpa-onnx, int8, כ-72 MB, ברישיון CC-BY-4.0.

---

## 1. מודלים מאומנים מראש לזיהוי אירועי קול

### 1.1 YAMNet (מאומת לעומק)

| נושא | ערך מאומת | מקור |
|---|---|---|
| ארכיטקטורה | MobileNet v1, 521 מחלקות של AudioSet | Kaggle API: `https://www.kaggle.com/api/v1/models/google/yamnet/get` |
| פרמטרים | "the classifier has 3.7M weights and performs 69.2M multiplies for each 960ms input frame" | https://github.com/tensorflow/models/tree/master/research/audioset/yamnet |
| קלט | 16 kHz mono, טווח [-1,1]. STFT עם חלון 25 ms ו-hop של 10 ms, 64 mel bins בתחום 125–7500 Hz. patch של 0.96 s עם hop של 0.48 s | README הנ"ל וכרטיס Kaggle |
| ביצועים (AudioSet eval) | "balanced average d-prime is 2.318, balanced mAP is 0.306, and the balanced average lwlrap is 0.393" | README הנ"ל |
| רישיון הקוד | Apache License 2.0. הקובץ חל על התיקיות "/community, /official, /orbit, /research…" | https://raw.githubusercontent.com/tensorflow/models/master/LICENSE |
| רישיון המשקולות ב-Kaggle | כל חמש הגרסאות (TF2, classification-tflite, tflite, tfjs, coral) מסומנות `Apache 2.0` | Kaggle API (שדה `licenseName`) |
| גודל הגרסאות (Kaggle `totalUncompressedBytes`) | TF2: 18,275,381; classification-tflite: 4,126,810; tflite: 16,096,668; tfjs: 16,139,075; coral (Edge TPU): 4,209,546 | Kaggle API |
| TFLite מכומת | "The model is quantized. This required retraining the model with Relu6… We now take a single fixed-length frame of audio (15600 samples) and return a single vector of scores for 521 audio event classes." | תיאור הגרסה ב-Kaggle API |
| ONNX | המרה רשמית של Qualcomm: `qualcomm/YamNet`. רישיון "mit" על הכרטיס, 3.73M פרמטרים, 14.2 MB ב-float, קלט 1x1x96x64 (log-mel, לא waveform). זמינות ONNX, QNN ו-TFLite בגרסאות float, w8a16 ו-w8a8. NPU ב-Snapdragon 8 Gen 1: ‏0.315 ms ב-float ו-0.194 ms ב-w8a8 | https://huggingface.co/qualcomm/YamNet |
| ONNX קהילתי | `andrelgomes/yamnet-onnx` ו-`funground-hq/yamnet-onnx` הופיעו בחיפוש בלבד. התוכן והרישיון not verified | תוצאות חיפוש |

**שימו לב:** ב-ONNX של Qualcomm הקלט הוא log-mel ולא waveform, ולכן צריך לממש את חישוב ה-mel בדיוק כמו ב-YAMNet. גרסת ה-TFLite של Google מקבלת waveform ישירות, וזה יתרון ברור.

**רישיון שמות המחלקות:** ה-AudioSet ontology מופץ ב-CC BY-SA 4.0. המקור הוא https://research.google.com/audioset/download.html, כפי שדווח על ידי תת-סוכן שפתח את הדף. לא אימתתי את הציטוט בעצמי.

#### 1.1.1 מחלקות רלוונטיות לסכנה: שמות ואינדקסים מדויקים

הרשימה נלקחה מ-`yamnet_class_map.csv` הרשמי (https://raw.githubusercontent.com/tensorflow/models/master/research/audioset/yamnet/yamnet_class_map.csv). הקובץ הורד ל-`research/models/yamnet/yamnet_class_map.csv`, ויש בו 521 שורות נתונים וכותרת `index,mid,display_name`.

**קבוצה A: קול אנושי במצוקה (מטרה ראשית)**

| index | mid | display_name |
|---|---|---|
| 6 | /m/07p6fty | Shout |
| 7 | /m/07q4ntr | Bellow |
| 9 | /m/07sr1lc | Yell |
| 10 | /t/dd00135 | Children shouting |
| 11 | /m/03qc9zr | Screaming |
| 19 | /m/0463cq4 | Crying, sobbing |
| 20 | /t/dd00002 | Baby cry, infant cry |
| 21 | /m/07qz6j3 | Whimper |
| 22 | /m/07qw_06 | Wail, moan |
| 33 | /m/07s2xch | Groan |
| 34 | /m/07r4k75 | Grunt |
| 39 | /m/07s0dtb | Gasp |
| 40 | /m/07pyy8b | Pant |

**קבוצה B: נשק ופיצוצים**

| index | mid | display_name |
|---|---|---|
| 420 | /m/014zdl | Explosion |
| 421 | /m/032s66 | Gunshot, gunfire |
| 422 | /m/04zjc | Machine gun |
| 423 | /m/02z32qm | Fusillade |
| 424 | /m/0_1c | Artillery fire |
| 425 | /m/073cg4 | Cap gun |
| 426 | /m/0g6b5 | Fireworks (hard negative / מבלבל) |
| 427 | /g/122z_qxw | Firecracker (hard negative / מבלבל) |
| 428 | /m/07qsvvw | Burst, pop |
| 430 | /m/07qqyl4 | Boom |

**קבוצה C: שבירה, אלימות פיזית ומכות**

| index | mid | display_name |
|---|---|---|
| 435 | /m/039jq | Glass |
| 437 | /m/07rn7sz | Shatter |
| 464 | /m/07pc8lb | Breaking |
| 463 | /m/07pjjrj | Smash, crash |
| 461 | /m/07ryjzk | Slap, smack |
| 462 | /m/07rdhzs | Whack, thwack |
| 460 | /m/07pws3f | Bang |
| 454 | /m/07qnq_y | Thump, thud |
| 352 | /m/07rjzl8 | Slam |
| 434 | /m/07qs1cx | Crack |

**קבוצה D: אזעקות (הקשר)**

| index | display_name |
|---|---|
| 382 | Alarm |
| 390 | Siren |
| 391 | Civil defense siren |
| 317 | Police car (siren) |
| 318 | Ambulance (siren) |
| 319 | Fire engine, fire truck (siren) |
| 304 | Car alarm |
| 393 | Smoke detector, smoke alarm |
| 394 | Fire alarm |
| 292 | Fire |

**קבוצה E: hard negatives ידועים מראש (להורדת התראות שווא או ל-gating)**

| index | display_name |
|---|---|
| 0 | Speech |
| 1 | Child speech, kid speaking |
| 13 | Laughter |
| 14 | Baby laughter |
| 61 | Cheering |
| 62 | Applause |
| 63 | Chatter |
| 64 | Crowd |
| 65 | Hubbub, speech noise, speech babble |
| 66 | Children playing |
| 267 | Video game music |
| 518 | Television |
| 519 | Radio |
| 494 | Silence |

**אין ב-YAMNet מחלקה ל-"fighting" או "violence".** קטטה תיבנה כצירוף של קבוצות A ו-C ברצף זמן, או ממסווג ייעודי שמאומן על ה-embeddings.

### 1.2 מודלים חלופיים: השוואה

| מודל | פרמטרים / גודל | AudioSet mAP (כפי שנראה במקור) | רישיון | קלט | התאמה ל-on-device | מקור |
|---|---|---|---|---|---|---|
| **YAMNet** | 3.7M; TFLite מכומת 4.1 MB | 0.306 (balanced) | Apache 2.0 | 16 kHz, waveform | **גבוהה**: TFLite רשמי, Coral, ו-ONNX/QNN של Qualcomm | ראו 1.1 |
| **PANNs** (audioset_tagging_cnn) | Zenodo: MobileNetV1 23.6 MB, MobileNetV2 20.8 MB, Cnn14 327.4 MB, Cnn14_16k 358.7 MB, Cnn10 25.2 MB, Cnn6 23.7 MB. ספירת הפרמטרים not verified | לפי שמות הקבצים: MobileNetV1 0.389, MobileNetV2 0.383, Cnn14 0.431, Cnn14_16k 0.438, Wavegram_Logmel_Cnn14 0.439 | קוד: MIT. משקולות ב-Zenodo: CC BY 4.0 | 32 kHz כברירת מחדל, ויש וריאנט ל-16 kHz | בינונית: PyTorch בלבד, והמרה ל-ONNX/TFLite תהיה עבודה שלנו | https://github.com/qiuqiangkong/audioset_tagging_cnn ו-https://zenodo.org/records/3987831 |
| **EfficientAT** (MobileNetV3 עם KD מ-AST) | mn04_as: 0.983M, mn05_as: 1.43M, mn10_as: 4.88M, dymn04_as: 1.97M, dymn10_as: 10.57M | mn04 43.2, mn05 44.3, mn10 47.1, dymn10 47.7 (%) | MIT | בקוד יש תיקייה `audio_32k`, כלומר כנראה 32 kHz (not verified במפורש). 128 mel, hop של 10 ms | **גבוהה פוטנציאלית**: מדויק בהרבה מ-YAMNet בגודל דומה. אין ONNX/TFLite רשמי (not mentioned), ונצטרך להמיר בעצמנו | https://github.com/fschmid56/EfficientAT |
| **AST** | not verified (ספירת הפרמטרים לא מופיעה ב-README) | single: 0.459; ensemble: 0.475 / 0.485 | BSD-3-Clause | 16 kHz | נמוכה: Transformer כבד | https://github.com/YuanGongND/ast |
| **BEATs** | not verified | "new state-of-the-art mAP 50.6% on AudioSet-2M for audio-only models" (abstract) | MIT (קובץ LICENSE של microsoft/unilm) | not verified | נמוכה: כבד. מתאים כמורה ל-distillation או כ-baseline במעבדה | https://arxiv.org/abs/2212.09058, https://github.com/microsoft/unilm/tree/master/beats |
| **Perch / Perch 2.0** | TF2: 99.8 MB (v1); perch_v2: 410 MB | לא מדווח על AudioSet. מודל ביואקוסטי (ציפורים ובעלי חיים) | Apache 2.0 (Kaggle API) | not verified | **לא רלוונטי**: מסווג כ-15,000 מינים ביולוגיים. לא מתאים לסכנה אנושית | Kaggle API `google/bird-vocalization-classifier` |

**מסקנה:**
- YAMNet הוא היחיד עם שרשרת deployment רשמית ומוכחת לטלפון (TFLite מכומת שמקבל waveform), ועם רישיון מתירני.
- EfficientAT mn04 או mn05 הוא המועמד לשדרוג ב-v2: mAP של 43–44% לעומת 30.6%, בגודל של כ-1M פרמטרים. ההמרה ל-ONNX/TFLite ושרשרת ה-mel דורשות עבודה ואימות.

---

## 2. דאטהסטים

הדאטהסטים אומתו על ידי תת-סוכן מחקר שפתח את הדפים, למעט היכן שצוין אחרת.
- **ESC-50 אומת ישירות:** README ו-LICENSE מ-GitHub הורדו ונבדקו.
- **AudioSet ו-FSD50K:** הציטוטים נמסרו מהתת-סוכן, ולא בדקתי אותם מחדש.

| # | דאטהסט | רישיון (כפי שנראה) | גודל | מחלקות רלוונטיות | תנאי הקלטה | הורדה |
|---|---|---|---|---|---|---|
| 1 | **AudioSet** https://research.google.com/audioset/download.html | התוויות: "Creative Commons Attribution 4.0 International (CC BY 4.0)". ה-Ontology: ‏CC BY-SA 4.0. האודיו שייך למעלי היוטיוב | 2,084,320 קטעים של 10 s, כ-5.8k שעות. balanced train: ‏22,176; eval: ‏20,383 (ה-CSV מציין 20,371); unbalanced: ‏2,042,985 | כל מחלקות סעיף 1.1.1 | YouTube, עולם אמיתי | רק CSV של YouTube IDs, והאודיו מורד עצמאית. מראה לא רשמי ב-HF ‏(`agkphysics/AudioSet`, כ-2.44 TB, הורד במרץ 2023) מחזיק כ-84–85% מהקטעים, למשל eval: ‏17,141 מתוך 20,371 |
| 2 | **FSD50K** https://zenodo.org/records/4060432 | "FSD50K is released under CC-BY", ולכל קטע רישיון Freesound משלו. dev: ‏CC0 ‏14,959 / CC-BY ‏20,017 / CC-BY-NC ‏4,616 / Sampling+ ‏1,374 | 51,197 קטעים, 108.3 שעות, 24.7 GB | 200 מחלקות. כולל Screaming, Shout, Yell, Gunshot_and_gunfire, Glass, Shatter, Explosion, Crying_and_sobbing, Fireworks, Siren, Slam, Crack. **חסרים:** Baby_cry, Whimper, Bang (נבדק במראה ב-HF) | Freesound, הקלטות אמיתיות ומגוונות | ישירות מ-Zenodo (zip מפוצל) |
| 3 | **ESC-50** https://github.com/karolpiczak/ESC-50 | "Creative Commons Attribution Non-Commercial license" (BY-NC 3.0). ESC-10 ב-CC BY. ייחוס לכל קטע בקובץ LICENSE (**אומת ישירות**) | 2,000 קטעים של 5 s, ‏44.1 kHz | glass_breaking, crying_baby, siren, fireworks, door_wood_knock, footsteps | Freesound | zip מ-GitHub |
| 4 | **UrbanSound8K** https://zenodo.org/records/1203745 | CC BY-NC 4.0, ויש לצטט את Salamon et al. 2014 | 8,732 קטעים של עד 4 s, ‏6.0 GB, 10 folds (אסור לערבב ביניהם) | gun_shot, siren, children_playing (hard negative), dog_bark | Freesound | Zenodo |
| 5 | **MIVIA Audio Events** https://mivia.unisa.it/datasets/audio-analysis/mivia-audio-events/ | **רישיון: not verified** (לא מופיע בדף). דורש רישום, ויש לצטט את Foggia et al., PRL 2015 | 6,000 אירועים: train כ-20 שעות, test כ-9 שעות | glass breaking, gunshots, screams | אירועים שהולבשו על רקע ב-SNR של 5–30 dB. מיקרופון Axis, ‏32 kHz | טופס בקשה |
| 6 | **DCASE2017 Task 2** (TUT Rare Sound Events) https://dcase.community/challenge2017/task-rare-sound-event-detection | Zenodo: "Other (Non-Commercial)". קובץ LICENSE.txt לא נקרא (not verified) | dev: כ-9 שעות רקע, כ-100 דוגמאות מבודדות לכל מחלקה, 500 mixtures לכל מחלקה | baby cry, glass break, gunshot | סינתטי: אירועי Freesound מעל רקעי TUT, אירוע אחד לכל היותר בכל mixture | Zenodo |
| 7 | **Audio-SAR-Dataset** https://zenodo.org/records/17800486 | CC BY 4.0 | 15,000 קטעים של 10 s | screaming, crying, knocking, coughing | סינתטי עם רעש מנוע, גשם ורוח | הגישה מוגבלת ודורשת בקשה |
| 8 | **ASVP-ESD** https://zenodo.org/records/4782712 | CC BY 4.0 (זכויות האודיו המקורי, מסרטים ויוטיוב, לא ברורות) | כ-10:51 שעות, ‏1.3 GB | scream, panic, pain, angry | סרטים, TV, YouTube | Zenodo |
| 9 | **Human Screaming Detection** (Kaggle, whats2000) | ב-API מופיע "MIT", אבל זו נגזרת של AudioSet, ולכן הרישיון בפועל מפוקפק | כ-5.68 GB; כ-862 קטעי צרחה ו-2,631 קטעים ללא צרחה | scream מול non-scream | YouTube | Kaggle (נדרשת התחברות) |
| 10 | **VocalSound** https://github.com/YuanGongND/vocalsound | CC BY-SA 4.0 | 21,024 הקלטות | צחוק, אנחה, שיעול וכו'. **אין מחלקות סכנה**, שימושי כ-hard negatives | crowdsourced | Dropbox / wget |
| 11 | **VSD (MediaEval)** https://www.interdigital.com/data_sets/violent-scenes-dataset | "for scientific research only". מחייב ציטוט | not verified | gunshot, explosion, scream | סרטי הוליווד (צריך לקנות את ה-DVD) ו-86 סרטוני YouTube | בקשה במייל |
| 12 | **XD-Violence** https://roc-ng.github.io/XD-Violence/ | **אין רישיון בדף** | 4,754 סרטונים, 217 שעות, עם אודיו | abuse, fighting, riot, shooting, explosion | not verified | Baidu, OneDrive |
| 13 | **ICSD** (infant cry) https://arxiv.org/html/2408.10561v3 | רישיון not verified | test של רקעים: 12 מחלקות, שעה לכל מחלקה, כולל "Watching TV", "Social activity", "Speech" ו-"Music" | infant cry | אמיתי וסינתטי | GitHub (not verified) |
| 14 | **דאטה עברי/ישראלי של מצוקה** | **לא נמצא** | | | | |

**דרישות ייחוס:**
- CC-BY ו-CC-BY-NC: יש לציין את שם היוצר ואת קישור Freesound לכל קטע. ב-ESC-50 וב-FSD50K הפרטים נמצאים בקבצי ה-LICENSE או ה-metadata.
- NC: מותר לפרויקט מחקרי לא מסחרי, אבל **אסור להעביר מודל שאומן על הדאטה למוצר מסחרי בלי בדיקה מחדש.**
- CC BY-SA: חל על ה-ontology של AudioSet ועל VocalSound, ומחייב share-alike על נגזרות של הדאטה עצמו.

---

## 3. מילות מפתח בעברית (KWS)

### 3.1 Whisper ועברית (מאומת מהמאמר)

**גדלי המודלים** (https://raw.githubusercontent.com/openai/whisper/main/model-card.md):

| מודל | פרמטרים |
|---|---|
| tiny | 39M |
| base | 74M |
| small | 244M |
| medium | 769M |
| large | 1,550M |
| turbo | 798M |

**ביצועים בעברית:**
- **WER בעברית ב-FLEURS**, מטבלה 13 "WER (%) on Fleurs" במאמר arXiv 2212.04356. הטבלה חולצה מ-HTML של ar5iv בפענוח טבלאות אוטומטי, כלומר אומתה מהטבלה הגולמית:

  | מודל | WER |
  |---|---|
  | tiny | **71.6** |
  | base | 61.7 |
  | small | 44.4 |
  | medium | 33.1 |
  | large | 30.2 |
  | large-v2 | 27.1 |

- סעיף 3.4 במאמר מונה את העברית בין החריגים שביצועיהם גרועים מהצפוי: "Hebrew (HE), Telugu (TE), Chinese (ZH), and Korean (KO)".
- **מספר שעות האימון בעברית:** not verified. הנתון מופיע רק באיור 11, שהוא תמונה.
- **השהיה של Whisper-tiny בטלפון:** not verified (לא נמדדה ולא נקראה).

**מסקנה:** Whisper-tiny אינו מתאים לזיהוי מילים בעברית בזמן אמת. WER של 71.6% פירושו שכמעט כל מילה שגויה.

### 3.2 דאטה עברי

| מקור | רישיון | גודל | הערות |
|---|---|---|---|
| **Mozilla Common Voice (he)**, גרסה 27.0 מ-2026-09-11 | הרישיון של MDC לא אומת. ההנחה המקובלת היא CC0, אבל ב-repo `cv-dataset` לא מופיע רישיון לדאטה: **not verified** | **7.21 שעות סה"כ, 5.34 שעות מאומתות, 60 דוברים, 5,654 קטעים** (מ-`cv-corpus-27.0-2026-09-11.json`) | https://github.com/common-voice/cv-dataset. ההורדה רק דרך Mozilla Data Collective. **קטן מאוד** |
| **ivrit.ai** (למשל crowd-transcribe-v5, 20 GB) | ivrit.ai License: v2 "may only be used, modified and distributed for the express purpose of either (a) training AI models, or (b) academic research". הדף מפנה גם ל-CC BY 4.0. אסור ליצור deepfake של קולות המשתתפים | crowd-transcribe-v5: ‏20 GB. מספר השעות not verified | https://www.ivrit.ai/en/the-license/, https://huggingface.co/datasets/ivrit-ai/crowd-transcribe-v5 (gated) |
| **Google FLEURS** | cc-by-4.0, כולל he_il | שעות not verified | https://huggingface.co/datasets/google/fleurs |
| **MLCommons MSWC** | CC-BY 4.0 | 50 שפות. **עברית אינה כלולה** | https://huggingface.co/datasets/MLCommons/ml_spoken_words |
| **Piper TTS** | repo `rhasspy/piper-voices` מסומן "mit". רישיון הקול הספציפי not verified | יש תיקייה `he/he_IL`. הקולות שבתוכה not verified | https://huggingface.co/rhasspy/piper-voices — לייצור דגימות סינתטיות של "הצילו" |

### 3.3 גישות KWS על המכשיר

1. **Google speech_embedding** (Kaggle `google/speech-embedding`)
   - רישיון Apache 2.0 (Kaggle API), גודל 2,789,125 בתים, גרסת TF1 בלבד.
   - הקלט 16 kHz. ה-embedding הראשון דורש 12,400 דגימות (775 ms), וכל embedding נוסף 1,280 דגימות (80 ms).
   - לפי המאמר (https://arxiv.org/abs/2002.01322): "a model which detects 10 keywords when trained on only synthetic speech" שקול למודל שאומן על יותר מ-500 דוגמאות אמיתיות. ללא ה-embedding נדרשות יותר מ-4,000 דוגמאות. המודלים הם בני כ-400k פרמטרים.
2. **openWakeWord** (https://github.com/dscripka/openWakeWord)
   - הקוד ב-Apache 2.0. **המודלים המאומנים ב-CC BY-NC-SA 4.0.**
   - משתמש ב-Google speech embedding. רץ על ONNX Runtime ועל TFLite (ב-Windows רק onnxruntime).
   - מאמן על דיבור סינתטי בלבד ("100% synthetic").
   - "openWakeWord only supports English", בגלל מודלי ה-TTS. הצינור עצמו אמור לעבוד גם עם TTS עברי (Piper he_IL), אבל זה **not verified**.
   - יעד: false-accept של פחות מ-0.5 לשעה.
3. **ASR עברי streaming** של ivrit.ai: `ivrit-ai/zipformer2-66m-streaming-he`
   - רישיון CC-BY-4.0, ‏66M פרמטרים.
   - קבצי sherpa-onnx ב-int8: encoder ‏70,478,964 בתים, decoder ‏1,049,105, joiner ‏771,805. סה"כ כ-72 MB, ולכן **לא הורד** (מעל 50 MB).
   - WER: ‏5.1 ב-eval-d1 ו-8.9 ב-eval-whatsapp. עם int8: ‏5.3 ו-8.8.
   - מפענח ב-chunks של 1.28 s.
   - מקור: https://huggingface.co/ivrit-ai/zipformer2-66m-streaming-he
   - אפשרות כבדה יותר אבל מדויקת: ASR מלא ואחריו התאמת מחרוזות. מתאים כ-stage 2, שמופעל רק אחרי טריגר אקוסטי.

---

## 4. מתודולוגיית הערכה

- **מדדי sed_eval** (https://tut-arg.github.io/sed_eval/sound_event.html):
  - שתי רמות הערכה: segment-based (רשת זמן קבועה) ו-event-based (התאמה ברמת מופע).
  - Error rate = (S+D+I)/N.
  - ברירות המחדל: `t_collar=0.2` s, `percentage_of_length=0.5`, `evaluate_offset=True`.
- **DCASE2017 Task 2 (rare events):**
  - המדד הראשי הוא event-based ER, ‏onset-only, עם collar של 500 ms. לצידו event-based F.
  - baseline על dev: ‏ER ‏0.53, ‏F ‏72.7%. לפי מחלקה: glass ‏0.22/88.5%, gunshot ‏0.69/57.4%, baby cry ‏0.67/72.0%.
  - המערכת המובילה: Lim_COCAI עם ER ‏0.1307 ו-F ‏93.1%.
  - **gunshot היא המחלקה הקשה ביותר.**
- **False alarms לשעה:**
  - ICSD (arXiv 2408.10561v3) מגדיר "false positive event (FPE) count per hour", בדומה ל-KWS.
  - סט הבדיקה בנוי משעה של רקע לכל אחת מ-12 מחלקות, ביניהן Watching TV, Social activity, Speech ו-Music.
  - מקורות ה-FP הנפוצים שדווחו: "music, noise, and speech".
- **Smartphone Audio Based Distress Detection** (arXiv 2608.04176, ‏"Always Alert"):
  - SVM דו-שלבי לצרחה ולבכי, ונבדק על שעות רבות של הקלטות מתנדבים בטלפון.
  - ניצול הרציפות בזמן של התראות השווא מוריד את ה-FAR.
  - התקורה דווחה כ-"about 1 facebook post every 3 to 4 hours".
  - נקרא רק ה-abstract. נתוני מיקום הטלפון והכיס: not verified.
- **openWakeWord** מודד false accepts על Dinner Party Corpus, כ-5.5 שעות far-field.

**מקורות FP צפויים** (משולב מהמקורות למעלה וממבחן ה-YAMNet שלי):
- TV, סרטים ומשחקי וידאו: יש בהם צרחות ויריות אמיתיות.
- ילדים משחקים וצועקים (מחלקות 10 ו-66).
- ספורט וקהל (61–64), זיקוקים (426–427), מוזיקה ואופרה.
  - במבחן שלי, סירנה מ-ESC-50 קיבלה Opera 0.668 לצד Siren 0.586.
- טריקות דלת (352).

**Domain shift (מסקנות שלי, לא ממקור):**
- מיקרופון של טלפון בכיס או בתיק מסנן תדרים גבוהים ומנחית.
- AGC ודיכוי הרעש של מערכת ההפעלה משנים את האות.
- ESC-50 ו-FSD50K הוקלטו בתנאים אחרים.
- **מה לבדוק:** להקליט סט מבחן במכשירי היעד בכמה מצבים (שולחן, כיס, תיק), ולמדוד אותו בנפרד.

---

## 5. פרטיות ומשפט (**אין לראות בזה ייעוץ משפטי**)

**חוק האזנת סתר, תשל"ט–1979.** הנוסח נלקח מ-ויקיטקסט, שמפנה לרשומות ולקבצי PDF של fs.knesset.gov.il. הקישור: https://he.wikisource.org/wiki/חוק_האזנת_סתר. הטקסט הגולמי הורד עם `action=raw`, והציטוט הוא מילה במילה.

> סעיף 1 – הגדרות:
> ”שיחה“ – בדיבור או בבזק, לרבות בטלפון, בטלפון אלחוטי ברדיו טלפון נייד, במכשיר קשר אלחוטי, בפקסימיליה, בטלקס, בטלפרינטר או בתקשורת בין מחשבים;
> ”בעל שיחה“ – כל אחד מאלה: (1) המדבר; (2) מי שהשיחה מיועדת אליו; (3) המשדר בבזק; (4) מי שהמסר המועבר בבזק מיועד להיקלט אצלו; למעט הנותן שירות של העברת מסר בבזק, למען זולתו או מטעם זולתו;
> ”האזנה“ – האזנה לשיחת הזולת, קליטה או העתקה של שיחת הזולת, והכל באמצעות מכשיר;
> ”האזנת סתר“ – האזנה ללא הסכמה של אף אחד מבעלי השיחה;

> סעיף 2(א): המאזין האזנת סתר שלא על פי היתר כדין, דינו – מאסר חמש שנים.
> סעיף 2(ג): המציב או המתקין מכשיר למטרת האזנת סתר שלא כדין או כדי לאפשר שימוש בו למטרה האמורה, דינו – מאסר חמש שנים.

**נקודות לבדיקה מול יועץ משפטי:**
- ניתוח רציף של אודיו בסביבת המשתמש עלול לכלול "קליטה… של שיחת הזולת… באמצעות מכשיר", כאשר המשתמש עצמו אינו "בעל שיחה" בשיחה של אחרים.
- האם עיבוד על המכשיר בלבד, בלי שמירה ובלי העתקה, ושמחלץ רק ציוני מחלקות לא-סמנטיים, משנה את הניתוח המשפטי? **לא נבדק. נדרשת חוות דעת.**
- מבחינה הנדסית המסקנה היא:
  - לא לשמור אודיו גולמי כברירת מחדל.
  - לא לתמלל דיבור באופן רציף. Hebrew ASR רק אחרי טריגר, ואולי רק ב-ring buffer קצר.
  - לקבל הסכמה מפורשת.

**פוינטרים נוספים (נקראו, אבל רק ברמת דף הנחיתה או כתבה):**
- תיקון 13 לחוק הגנת הפרטיות: "היום, 14.8.2025, נכנס לתוקפו תיקון 13 לחוק הגנת הפרטיות" (https://www.law.co.il/news/2025/08/14/ppa-is-updating-its-guidelines-following-amendment-13/). התיקון מרחיב את הגדרות החוק ואת תחולתו. **הנוסח הרשמי לא נקרא.**
- EDPB Guidelines 02/2021 on virtual voice assistants, גרסה סופית מ-07.07.2021 (https://www.edpb.europa.eu/our-work-tools/our-documents/guidelines/guidelines-022021-virtual-voice-assistants_en). נקרא רק דף הנחיתה, וה-PDF לא נקרא. רלוונטי בעיקר אם יהיו משתמשים ב-EU.

---

## 6. המלצה ל-v1

**מודל:**
- **שלב 1 (טריגר אקוסטי):** YAMNet float TFLite (‏`tflite`, 16 MB), שמחזיר scores ו-embeddings בקריאה אחת.
  - בדסקטופ הוא רץ עם `ai-edge-litert`. בטלפון עם LiteRT (Android) או אחרי המרה ל-CoreML (iOS; המסלול not verified).
  - אפשר להחליף אותו בגרסה המכומתת, 4 MB, כשמשתמשים רק בציונים.
- **ראש ייעודי:** logistic regression או MLP קטן על ה-embedding בגודל 1024. המחלקות:
  - `scream_shout`
  - `cry_plead`
  - `glass_break`
  - `gunshot_explosion`
  - `impact_fight`
  - `background`
- **החלקה בזמן:**
  - חלון של 0.96 s עם hop של 0.48 s.
  - טריגר רק אם k מתוך n חלונות עברו את הסף, בדומה לרעיון ה-time-contiguity ב-Always Alert.
  - gating לפי ציוני Television ו-Radio של YAMNet (518, 519).
- **שלב 2 (מילות מפתח בעברית):**
  - מסווג KWS על Google speech_embedding.
  - האימון על דגימות "הצילו", "עזרה", "תעזבו אותי" ו-"די", שמגיעות מ-Piper he_IL (לבדוק רישיון), מהקלטות מתנדבים בהסכמה, ומ-Common Voice he.
  - הנגטיבים מ-ivrit.ai ומ-FLEURS he_il.
  - חלופה כבדה: zipformer2 streaming של ivrit.ai, שמופעל רק אחרי טריגר.

**דאטה לאימון הראש:**
- **פוזיטיבים:** FSD50K (Screaming, Shout, Yell, Crying_and_sobbing, Glass, Shatter, Gunshot_and_gunfire, Explosion), ESC-50, DCASE2017 Task 2, MIVIA (אם תאושר גישה), ו-AudioSet balanced, כמה שעוד זמין.
- **נגטיבים:** קטעים "ניטרליים" מ-FSD50K, UrbanSound8K (children_playing, street_music), VocalSound, הקלטות TV, ספורט וזיקוקים.
- **עקרונות:**
  - לשמור את חלוקות ה-folds המקוריות (ב-UrbanSound8K זו דרישה מפורשת).
  - לא לערבב קטעים מאותו מקור Freesound בין train ל-test.

**תוכנית הערכה:**
1. **Clip-level:** ‏AP/F1 לכל מחלקה, על folds של ESC-50 ו-US8K ועל ה-eval של FSD50K.
2. **Event-level:** ‏sed_eval ב-event-based, ‏onset-only, עם collar של 500 ms, על DCASE2017 Task 2. השוואה ל-baseline: ‏ER ‏0.64 ו-F ‏64.1% על eval.
3. **False alarms לשעה:**
   - לפחות 10–20 שעות "חיים רגילים" מוקלטות בטלפון: TV, ילדים, ספורט, מסעדה, רחוב, כיס.
   - באישור המשתתפים, ועם שמירה רק לצורך המחקר.
   - יעד ראשוני: פחות מ-0.5 FA לשעה, בדומה ליעד של openWakeWord. זה יעד הנדסי ולא נתון ספרותי לתחום הזה.
4. **Domain shift:** השמעה חוזרת (re-recording) של קטעי מבחן דרך רמקול אל מיקרופון הטלפון, בתנוחות שולחן, כיס ותיק. מדווחים את הירידה בביצועים.
5. **השהיה וסוללה:** מדידה במכשירים אמיתיים, כרגע not verified.

**מה לבדוק בהמשך:**
- (א) רישיון MIVIA ו-LICENSE.txt של DCASE2017.
- (ב) תנאי הרישיון של Common Voice ב-MDC.
- (ג) הקולות ב-Piper he_IL והרישיון שלהם.
- (ד) המרת EfficientAT mn05 ל-TFLite/ONNX, והשוואה מול YAMNet על אותו סט.
- (ה) שימוש ב-speech_embedding בלי TF1. openWakeWord מספק מימוש ONNX משלו. **רישיון המודלים שלו NC.**
- (ו) חוות דעת משפטית.

---

## 7. מבחן הרצה אמיתי (בוצע)

**סביבה:**
- Python 3.11.0, ב-venv של הפרויקט.
- הותקן `ai-edge-litert==2.3.0`, wheel רשמי `cp311-win_amd64`. נמשכו איתו גם `flatbuffers`, `ml_dtypes`, `protobuf`, `tqdm` ו-`backports.strenum`.
- numpy 2.4.6 קיים ולא שונה.
- TensorFlow ו-PyTorch **לא** הותקנו.

**קבצים שהורדו** (תיקייה `research/models/`):

| קובץ | גודל | SHA256 | מקור |
|---|---|---|---|
| `yamnet/yamnet_classification.tflite` | 4,126,810 B | `10C95EA3EB9A7BB4CB8BDDF6FEB023250381008177AC162CE169694D05C317DE` | `https://tfhub.dev/google/lite-model/yamnet/classification/tflite/1?lite-format=tflite`, הפניה ל-storage של Kaggle |
| `yamnet/yamnet_float_embeddings.tflite` | 16,096,668 B | `141FBA1CDAAE842C816F28EDC4937E8B4F0AF4C8DF21862CCC6B52DC567993C3` | `https://tfhub.dev/google/lite-model/yamnet/tflite/1?lite-format=tflite` |
| `yamnet/yamnet_class_map.csv` | | | GitHub tensorflow/models |
| `testclips/esc50.csv` | | | metadata של ESC-50 |
| `testclips/1-20133-A-39.wav` | | | ESC-50, glass_breaking. מקורו "breaking_glass.wav" של gezortenplotz ב-Freesound, ‏CC-BY |
| `testclips/1-187207-A-20.wav` | | | ESC-50, crying_baby. "More baby noises" של k3pp, ‏CC0 |
| `testclips/1-31482-A-42.wav` | | | ESC-50, siren. "20070224.siren.flac" של dobroide, ‏CC-BY |

הייחוסים נלקחו מקובץ LICENSE של ESC-50. הדאטהסט כולו ב-CC BY-NC 3.0.

**מבנה המודלים:**
- המודל המכומת: קלט `waveform_binary` [15600] float32, ופלט [1,521].
- מודל ה-float, על קלט של 3 s: scores (6,521), embeddings (6,1024), log-mel (336,64). זמן ריצה כ-22 ms.

**תוצאות** (המודל המכומת; חלונות של 15,600 דגימות, hop של 7,680; max-pooled top-5; השהיה ממוצעת של כ-3–5 ms לחלון במעבד):

| קלט | top-5 (max over windows) |
|---|---|
| סינוס 1 kHz, ‏3 s | 475 Beep, bleep 0.738; 498 Sound effect 0.082; 495 Sine wave 0.059 |
| רעש לבן, ‏3 s | 509 Static 0.738; 507 Noise 0.668; 514 White noise 0.668 |
| glass_breaking | 494 Silence 0.801; 345 Engine starting 0.500; **464 Breaking 0.414**; **434 Crack 0.332**; 294 Vehicle 0.332 |
| crying_baby | 0 Speech 0.586; 13 Laughter 0.414; 80 Caterwaul 0.262. **מחלקה 20 לא בטופ-5: החטאה** |
| siren | 233 Opera 0.668; **390 Siren 0.586**; **382 Alarm 0.586**; 232 Classical music 0.500; 316 Emergency vehicle 0.414 |

**פרשנות:**
- המודל נטען ורץ כשורה, ותוצאות הסינוס והרעש סבירות.
- על קטעים אמיתיים הוא מזהה חלקית (Breaking, Siren) ומבלבל עם מוזיקה ודיבור.
- הציונים מתפלגים בקפיצות בדידות (0.738, 0.668, 0.586…), ככל הנראה בגלל הכימות.
- **מכאן ההמלצה לאמן ראש ייעודי על ה-embeddings, ולא להסתמך על ספים על הציונים הגולמיים.**
- ה-resampling מ-44.1 kHz ל-16 kHz בוצע ב-FFT ב-numpy, ולכן ייתכנו הבדלים קלים מול resampler סטנדרטי.

הסקריפט שבו השתמשתי נמצא ב-scratchpad, מחוץ לפרויקט.

---

## 8. מה לא אומת (סיכום)

- **YAMNet ומודלים חלופיים:**
  - ה-ONNX הקהילתיים של YAMNet.
  - מספר הפרמטרים של AST, BEATs ו-PANNs-MobileNet.
  - קצב הדגימה של EfficientAT (רק מסקנה מ-`audio_32k`).
  - המסלול ל-CoreML.
- **רישיונות דאטהסטים:** רישיון MIVIA, ‏LICENSE.txt של DCASE2017, רישיון ICSD.
- **עברית:**
  - רישיון Common Voice ב-MDC.
  - קולות Piper he_IL והרישיון שלהם.
  - מספר השעות של ivrit.ai ו-FLEURS בעברית.
  - שעות האימון בעברית ב-Whisper.
  - השהיה של Whisper-tiny או של zipformer בטלפון.
- **הערכה:** פרטי ה-FA לשעה ב-ICSD (באיור בלבד), והפרטים המלאים של Always Alert.
- **משפט:** הנוסח הרשמי של תיקון 13, ו-PDF ההנחיות של EDPB.
