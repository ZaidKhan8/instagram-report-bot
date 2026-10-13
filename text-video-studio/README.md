# Text → Video Studio

Apna text likhiye → video ban jaayegi. **Bilkul free, unlimited, aur hamesha ke liye.**
Na credit, na limit, na subscription, na internet — kyunki video **aapke hi device par**
banta hai (aapka phone ya computer), kisi company ke server par nahi.

```
text (Hindi/English)  ──►  MP4 video
                           9:16 Reels/Shorts · 16:9 YouTube · 1:1 post
                           animation + theme + background image + music + voiceover
```

Do tarike se chalaya ja sakta hai:

1. **Web studio** — browser me khulne wala simple UI (`studio.py`)
2. **Command line** — ek project file se seedha MP4 (`render.py`)

---

## Termux (phone) par ek-ek line

F-Droid/GitHub se **Termux** install kariye, kholiye, phir:

```bash
pkg update -y && pkg install -y python git ffmpeg python-pillow harfbuzz espeak
```

```bash
git clone -q -b arena/e256f896-instagram-report-bot https://github.com/ZaidKhan8/instagram-report-bot && cd instagram-report-bot/text-video-studio
```

```bash
python get_fonts.py
```

```bash
python studio.py
```

Ab browser me kholiye: **http://localhost:8000**
(Phone me Chrome kholkar yahi address likhiye. Band karne ke liye Termux me `Ctrl+C`.)

Chhote video ke liye settings me **Fast (540p)** chuniye — phone par wahi tez chalta hai.

### Seedha command line se (bina browser)

```bash
python render.py --demo -o pehla-video.mp4
termux-open pehla-video.mp4
```

---

## Computer (Windows / Mac / Linux) par

```bash
pip install pillow imageio-ffmpeg
python get_fonts.py
python studio.py
```

`imageio-ffmpeg` se ffmpeg khud aa jaata hai, alag install karne ki zarurat nahi.
(Chaahein to system ka ffmpeg bhi kaam karega.)

Browser: **http://localhost:8000**

---

## Apni video kaise banaiye (web studio)

1. **Scene** = ek card jisme text hai. 3 scene pehle se hote hain — unme apna text likhiye.
2. Har scene me:
   - **Text** — bade akshar me dikhta hai (Hindi aur English dono chalti hai)
   - **Subtitle** — chhota text niche
   - **Kitne second** — kitni der dikhe
   - **Animation** — `slide-up`, `fade`, `pop`, `typewriter`, `none`
   - **Transition** — pichhle scene se kaise badle: `fade`, `slide`, `zoom`, `none`
   - **Image** — background photo (optional; na do to rang ka gradient aata hai)
3. **Theme** aur rang apni pasand se badliye, ya preset chuniye.
4. **Voiceover** chaahiye to tick kariye — text bol kar sunaya jaayega (espeak).
5. **Background music** — apni MP3/any audio file. **Dhyan:** sirf wahi music daaliye
   jo aapko use karne ki ijazat hai (YouTube paise wala music nahi, warna video
   strike ho sakti hai). Free music: YouTube Audio Library, Pixabay Music, Free Music Archive.
6. **Preview** dabaiye — pehle scene ka still dikh jaayega.
7. **Video banao** dabaiye — progress bar chalega, phir **Download MP4**.

Project **Save/Load** bhi kar sakte hain (JSON file `projects/` folder me).

---

## Command line (project file se)

`render.py` ek JSON file leta hai:

```json
{
  "aspect": "9:16",
  "quality": "medium",
  "fps": 30,
  "theme": "midnight",
  "music": "song.mp3",
  "music_volume": 0.25,
  "voiceover": { "engine": "espeak", "voice": "hi", "speed": 150, "auto_extend": true },
  "scenes": [
    { "text": "नमस्ते दोस्तों", "sub": "आज की बात", "duration": 3, "animation": "slide-up" },
    { "text": "Unlimited video", "sub": "kyunki limit hai hi nahi", "duration": 3, "animation": "pop",
      "image": "photo.jpg", "transition": "zoom" }
  ]
}
```

```bash
python render.py mera-project.json -o video.mp4
python render.py --check          # sab theek hai ya nahi, ye batata hai
python render.py --demo -o demo.mp4
```

Dusre option: `--aspect 9:16|16:9|1:1`, `--quality fast|medium|high` (JSON se), `--theme`,
`--font`, `--voiceover` — sab JSON me hi likhiye.

---

## Hindi sahi dikhe — ye kaise hua

Normal Pillow (`pip install pillow`) me **raqm** nahi hota, isliye "कि" jaise akshar
galat chhapte hain (matra aage chali jaati hai) aur क्ष / त्र / ज्ञ ke ligature nahi bante.

Isliye is project me `textshape.py` likha gaya hai jo:

1. **HarfBuzz** ko ctypes se use karta hai (jo Pillow ke saath hi aati hai, ya Termux me
   `pkg install harfbuzz` se aa jaati hai) — akshar ki sahi shaping ke liye,
2. glyph ke **outlines khud padhta hai** (`glyf` table) aur unhe khud rasterize karta hai.

Result: Hindi theek dikhti hai — bina kuch compile kiye, bina raqm ke.
Agar HarfBuzz na mile to app band nahi hoti, sirf ek warning deti hai.

---

## Kya-kya ban sakta hai

- Instagram Reels / YouTube Shorts (9:16), YouTube (16:9), square post (1:1)
- Text animation: slide-up, fade, pop, typewriter
- Scene transitions: fade, slide, zoom
- Gradient background ya apni photo
- Background music + espeak voiceover (Hindi/English)
- Unlimited videos — jitni baar chahein, jitne chahein

## Kya nahi hai (saaf-saaf)

- **Yeh AI generative model nahi hai.** Yeh ek text-animation + slideshow engine hai:
  aapka text animations/transitions ke saath video ban jaata hai, aapki photos bhi
  lag sakti hain. Text ko padh kar naya footage "bana" deta hua AI model aapke phone
  par nahi chal sakta — wo bahut bhaari hota hai aur uske liye GPU/cloud chahiye.
  Isliye is tool me: **koi credit system hi nahi hai** — unlimited ka matlab yahan
  sach me unlimited hai.
- Kisi paid AI service ke credits ko "free unlimited" banana isme shamil nahi hai
  (wo us service ke saath dhokha hota, aur account band ho jaata).

## Troubleshooting

| Dikkat | Hal |
|---|---|
| `ffmpeg nahi mila` | Termux: `pkg install ffmpeg` · PC: `pip install imageio-ffmpeg` |
| `pillow install nahi hai` | Termux: `pkg install python-pillow` · PC: `pip install pillow` |
| Hindi galat dikh rahi hai | `pkg install harfbuzz` (Termux) · PC: `pip install --upgrade pillow` |
| Koi bar bar crash ho raha hai? | `python render.py --check` chalaiye aur output bhejiye |
| Render bahut dheema hai | Quality `fast` (540p) kariye, fps 24 kariye, chhote video banaiye |
| Music video me nahi aa raha | Audio file MP3/WAV/M4A ho; pehle `--check` se ffmpeg confirm kariye |
| Voiceover nahi aa raha | `pkg install espeak`, phir Voiceover wala box tick kariye |
| Port busy | `python studio.py --port 8100` |

## Files

```
text-video-studio/
├── studio.py        # web studio (browser UI + API) — python3 studio.py
├── render.py        # video engine: frames + audio + ffmpeg → MP4
├── textshape.py     # Hindi/complex-script shaping (HarfBuzz + apna rasterizer)
├── get_fonts.py     # Noto fonts laata hai (SIL OFL — free)
├── web/index.html   # UI (ek file, koi CDN nahi)
├── fonts/           # .ttf files
├── projects/        # render jobs + saved projects (git me nahi jaate)
└── demo-project.json
```

All the best — aur haan, video banane ke baad `mp4` seedha phone se share kar sakte hain. 🎬
