# دستور کار Codex / Astra — بازبینی و ارتقای LlamaForge Suite 0.33.0

این پروژه را صرفاً از روی README قضاوت نکن. ابتدا کد واقعی، تست‌ها، مسیرهای اجرا و رفتار Runtime را بررسی کن و بعد تغییر بده. هدف، بازطراحی کور یا افزودن Feature نمایشی نیست؛ هدف این است که **Skill/Agent System و Adaptive Execution Engine واقعاً هوشمندتر، سریع‌تر، پایدارتر و قابل اتکاتر شوند** و در عین حال تمام قابلیت‌های فعلی حفظ شوند.

نسخه مبنا: **0.33.0-adaptive-engine**

## 1) قبل از هر تغییر

ابتدا این فایل‌ها و مسیرها را بخوان و Data Flow واقعی را ترسیم کن:

- `llamaforge/llamaforge/core/skill_system.py`
- `llamaforge/llamaforge/core/agent_engine.py`
- `llamaforge/llamaforge/core/agent_tools.py`
- `llamaforge/llamaforge/core/workspace.py`
- `llamaforge/llamaforge/core/planner.py`
- `llamaforge/llamaforge/core/autotune.py`
- `llamaforge/llamaforge/core/runtime.py`
- `llamaforge/llamaforge/core/hardware.py`
- `llamaforge/llamaforge/web/server.py`
- `llamaforge/llamaforge/web/static/app.js`
- `llamaforge/llamaforge/web/static/styles.css`
- `llamaforge/SKILL_SYSTEM_FA.md`
- `llamaforge/AGENT_SKILLS_FA.md`
- تمام تست‌های مرتبط در `llamaforge/tests/`

قبل از Refactor، کل Test Suite را اجرا کن و نتیجه Baseline را ثبت کن. نسخه 0.33 هنگام تحویل **189 تست موفق** داشته است. اگر تعداد تست‌ها در ریپو تغییر کرده، ملاک این است که همه تست‌های موجود پاس شوند و هیچ Regression پنهانی ایجاد نشود.

هرجا Documentation با کد واقعی اختلاف داشت، **کد و رفتار واقعی Runtime را منبع حقیقت بدان** و سپس Documentation را اصلاح کن.

---

# بخش A — Smart Skill / Agent System

## 2) مشکل اصلی که باید حل شود

مدل نباید برای استفاده از Skillها به چند Keyword خشک وابسته باشد. سیستم باید اول بفهمد کاربر واقعاً چه می‌خواهد، سپس Capability مناسب را در اختیار مدل قرار دهد و اجازه دهد مدل بین Skillهای مرتبط تصمیم بگیرد.

نمونه‌های اجباری:

- «امروز چندمه؟» → Time/Calendar باید در دسترس باشد.
- «ساعت چنده؟» → Time Skill.
- «برای فردا یک قرار بساز» → Calendar write محلی؛ نباید به مجوز نوشتن روی سایت خارجی وابسته باشد.
- «این ZIP چیه؟» → فقط Metadata/Probe و لیست امن؛ کل محتوا خودکار خوانده نشود.
- «کدهای داخل این ZIP را بررسی کن» → بعد از Probe، فقط محتوای لازم خوانده شود.
- «این فایل را ذخیره کن» → Attachment/Workspace.
- «متن PDF را خلاصه کن» → File extraction/read.
- یک URL معمولی → Web read.
- «روی سایت برو، کلیک کن و فرم پر کن» → Browser action با Permission مناسب.
- پیام ساده‌ای مثل «سلام» نباید بی‌دلیل Skill Pipeline سنگین را فعال کند.

سیستم باید روی فارسی، انگلیسی و متن مخلوط فارسی/انگلیسی تست شود.

## 3) معماری Skillها را Audit کن

معماری فعلی Capability Family را حفظ کن، ولی بررسی کن آیا می‌توان آن را تمیزتر و Typed کرد:

- Live Web
- Time & Calendar
- Files & Attachments
- Browser
- API
- Extensions

هر Skill بهتر است Manifest استاندارد داشته باشد که حداقل این اطلاعات را مشخص کند:

- نام و توضیح کوتاه
- Capability family
- ورودی Typed
- خروجی Typed
- Preconditions
- Side effects
- Read-only یا Write
- Local write یا External write
- Parallel-safe بودن
- Idempotent بودن
- Timeout
- Retry policy
- Cost/latency hint
- نیاز به Network
- نیاز به Approval/Permission
- Verification strategy

هدف این است که Planner مجبور نباشد رفتار Skill را از متن آزاد حدس بزند.

## 4) Router و Shortlist

`skill_system.py` و `agent_engine.py` را عمیق بررسی کن.

می‌خواهم:

1. Intent از **معنای درخواست** استخراج شود، نه صرفاً وجود کلمات.
2. Runtime Guard فقط جلوی حذف شدن Capability واضح را بگیرد؛ تصمیم نهایی مدل را بی‌دلیل Override نکند.
3. همه Skillها داخل Prompt مدل Dump نشوند. ابتدا Capability Family، بعد Shortlist کوچک.
4. اگر مدل کوچک Router اشتباه کرد، fallback قطعی برای موارد واضح وجود داشته باشد.
5. اگر درخواست واقعاً Direct است، Agent Loop بیهوده اجرا نشود.
6. اگر یک Skill شکست خورد، سیستم بدون دلیل همان Call یکسان را پشت‌سرهم تکرار نکند.
7. Planner بتواند بعد از Observation تصمیم جدید بگیرد.
8. Verification قبل از Final برای عملیات مهم انجام شود.

برای Router یک مجموعه تست جدی Table-driven بساز، مخصوصاً برای جمله‌های مشابه ولی با Intent متفاوت.

## 5) فایل‌ها و Attachmentها

LlamaForge باید **هر نوع فایل** را بپذیرد، حتی اگر Parser آن فرمت را نداشته باشد.

قاعده اصلی:

**Accept first → Identify/Probe → Understand user intent → Open/Extract only if required**

نباید صرفاً به خاطر Attach شدن فایل، محتوای کامل آن وارد Context شود.

برای فایل‌ها بررسی کن:

- ZIP / TAR / archives
- TXT / Markdown / JSON / YAML / XML
- Source code
- PDF
- DOCX
- XLSX
- PPTX
- Images
- Audio
- Video
- Binary/unknown

برای فایل ناشناخته حداقل نام، MIME، Size، Extension، Hash و Metadata قابل دسترس باشد.

برای Archive:
- ابتدا فقط listing امن.
- Path traversal و zip bomb کنترل شود.
- استخراج فقط در Sandbox/Workspace امن.
- متن تمام فایل‌ها بدون نیاز کاربر وارد Prompt نشود.

برای Office/PDF/Audio/Video اگر Extractor نصب نیست، سیستم نباید دروغ بگوید «به فایل دسترسی ندارم». باید دقیق بگوید فایل دریافت شده ولی برای آن عملیات مشخص چه Capability/Extractor لازم است.

## 6) Permission Model را خراب نکن

دو Permission باید مستقل بمانند:

1. **Local Workspace/Calendar changes**
2. **External website actions**

خاموش بودن External Write نباید باعث شود ساخت رویداد تقویم یا ویرایش فایل محلی غیرممکن شود.

Permissionها را در سطح Operation بررسی کن، نه فقط Skill.

هیچ Refactorی نباید دوباره Calendar/File write را به Site write وابسته کند.

## 7) Parallel Agent/Tool Execution

کارهای Read-only مستقل را در صورت سود واقعی موازی کن:

- چند Web read مستقل
- چند File read مستقل
- Metadata extraction
- Search
- API read

اما:

- Writeهای وابسته را موازی نکن.
- عملیات دارای Side Effect را بدون Transaction/Idempotency موازی نکن.
- یک مدل اصلی را برای هر Agent دوباره در RAM Load نکن.

هدف این است که Agentها از **Shared Model Engine** استفاده کنند و فقط Tool Work موازی شود.

بررسی کن که Cancellation، Timeout و Error propagation در Parallel tasks درست باشد.

---

# بخش B — Adaptive Execution Engine

## 8) هدف Adaptive Engine

هدف نمایش CPU=100% یا GPU=100% نیست.

هدف واقعی:

- بیشترین Generation tok/s
- بیشترین Prompt Processing tok/s
- کمترین TTFT
- کمترین RAM pressure
- جلوگیری از Swap/OOM
- پایداری طولانی‌مدت
- امکان اجرای مدل‌های بزرگ‌تر روی سخت‌افزار محدود

سیستم هدف اصلی فعلی:
- Windows
- حدود 16GB RAM
- CPU کم‌هسته
- iGPU مانند Intel HD 530 با Shared RAM
- GGUFهای کوچک تا حدود 10–12GB

ولی طراحی نباید فقط به همین سخت‌افزار Hard-code شود.

## 9) AutoTune را عمیق‌تر کن

`autotune.py` را بررسی کن.

در نسخه فعلی Thread و GPU layer عملاً Benchmark می‌شوند، اما Batch/UBatch هنوز به اندازه کافی Search واقعی ندارند. این را کامل کن.

پارامترهای قابل بررسی:

- CPU threads
- threads-batch
- n-gpu-layers
- batch-size
- ubatch-size
- KV cache type
- parallel slots
- load mode
- context profile در محدوده امن
- speculative mode در صورت پشتیبانی

ولی Cartesian Product عظیم نساز.

روش مناسب:
1. Hardware/model fingerprint
2. Warm-up
3. Coarse search
4. حذف ترکیب‌های ضعیف
5. Fine search اطراف بهترین‌ها
6. چند Repeat
7. Median/percentile به جای یک عدد تصادفی
8. بررسی Stability و Memory pressure

نتیجه AutoTune باید برای **همان مدل و همان محیط معتبر** Cache شود.

Fingerprint حداقل شامل این‌ها باشد:

- Model path/size/mtime یا Hash مناسب
- Quantization
- llama.cpp runtime build
- CPU
- GPU
- Driver/backend
- RAM
- Context/load profile مهم

اگر Runtime یا Driver یا مدل تغییر کرد، نتیجه Stale نباید کورکورانه استفاده شود.

## 10) امتیازدهی Benchmark

فقط Generation speed را نگاه نکن.

حداقل این Metricها را ثبت کن:

- Prompt tok/s
- Generation tok/s
- TTFT
- Peak process RAM
- Shared GPU memory در صورت امکان
- Load time
- Failure/OOM
- CPU/GPU utilization در صورت قابل دسترس بودن
- Stability

سه Profile اختیاری می‌تواند مفید باشد:

- Interactive: اولویت TTFT و Generation
- Throughput: اولویت مجموع tok/s
- Fit Largest Model: اولویت RAM safety و پایداری

اگر UI اضافه می‌کنی، Default باید ساده بماند و کاربر عادی مجبور به فهم همه این اصطلاحات نباشد.

## 11) مدل‌های 10–12GB روی 16GB RAM

برای مدل بزرگ، Full RAM را کورکورانه اجبار نکن.

Engine باید با Headroom واقعی تصمیم بگیرد:

- مدل کوچک + RAM کافی → `load-mode none` / Full RAM
- مدل بزرگ → mmap
- Context تطبیقی
- Batch/UBatch کوچک‌تر
- KV cache فشرده‌تر در صورت نیاز
- Prompt cache در فشار حافظه محدود/خاموش
- Parallel slots کمتر

هدف این است که Windows و iGPU Shared Memory هم فضای امن داشته باشند.

اگر تنظیم ذخیره‌شده کاربر مثلاً Context=8192 است ولی مدل بزرگ فقط با 4096 امن اجرا می‌شود، مقدار ذخیره‌شده کاربر را خراب نکن؛ فقط **Effective runtime context** را محدود کن و این موضوع را واضح در UI نشان بده.

## 12) Lazy Vision / mmproj

این Constraint مهم است:

اگر پیام فقط متن دارد، `mmproj` نباید Load شود.

فقط اولین درخواست واقعی Vision باعث Load/Reload لازم شود.

بررسی کن:
- اولین تصویر
- چند تصویر
- برگشت به متن
- Model switch
- Failed projector load

هیچ تغییر جدیدی نباید دوباره mmproj را همیشه Load کند.

## 13) Speculative Decoding

Speculative را صرفاً چون Runtime Flag دارد روشن نکن.

آن را با workload واقعی Benchmark کن.

در صورت امکان:
- n-gram speculation بدون مدل دوم
- draft model کوچک فقط اگر RAM/CPU اجازه داد

برای سیستم ضعیف، اگر Draft Model خودش CPU را اشباع کرد و سرعت را کم کرد، Adaptive باید آن را خاموش کند.

Speculative benchmark را از `llama-bench` کورکورانه نتیجه‌گیری نکن اگر آن مسیر واقعاً Speculative را اندازه نمی‌گیرد. از مسیر واقعی `llama-server` یا `llama-cli` زمان‌گیری End-to-End انجام بده.

---

# بخش C — Shared Model Engine و Concurrency

## 14) یک مدل، چند کار

پیش‌فرض نباید چند `llama-server.exe` با کپی‌های جدا از یک مدل باشد.

بررسی کن که:

- Agentهای مختلف بتوانند از همان Model Engine استفاده کنند.
- Continuous batching / parallel slots فقط وقتی RAM headroom وجود دارد فعال شود.
- مدل بزرگ روی 16GB ترجیحاً `parallel=1` بماند.
- مدل کوچک در صورت Benchmark مثبت بتواند slot بیشتری بگیرد.
- Queueing، Cancellation و Fairness مشخص باشند.
- یک Agent طولانی بقیه درخواست‌ها را برای همیشه قفل نکند.

---

# بخش D — Streaming

## 15) Streaming واقعی را Audit کن

Direct chat و Final answer ایجنت باید Token/Delta واقعی را از `llama-server` Stream کنند.

اما Structured Planner/Tool JSON را نصفه‌نصفه به Executor نده.

بررسی کن:

- Token ordering
- Duplicate delta
- SSE reconnect
- Long-poll fallback
- Cancel
- Client disconnect
- Error event
- Final marker
- Agent tool progress
- Final answer streaming

Streaming نباید صرفاً متن کامل‌شده را بعداً با Timer تکه‌تکه نمایش دهد.

---

# بخش E — Web UI مرتبط

UI را فقط در بخش‌هایی تغییر بده که با این معماری مرتبط هستند.

موارد مهم:

- Skill Tree کوچک، تمیز و قابل فهم
- Capability cardها به جای لیست عظیم Toolها
- Context field نباید با State refresh مقدار کاربر را برگرداند
- CPU / GPU / Hybrid / Max Both / Adaptive واضح باشند
- Effective settings از Saved settings تفکیک شوند
- AutoTune progress واضح باشد
- Runtime download درصد/حجم/سرعت/ETA داشته باشد
- Load Model هیچ‌وقت بدون Feedback ظاهری نماند
- Streaming نرم و قابل Cancel باشد
- Attachment هر نوع فایل قابل انتخاب باشد

UI باید Responsive، جمع‌وجور و بدون Dashboard شلوغ باشد.

---

# بخش F — Constraintهای غیرقابل شکستن

این موارد را به هیچ عنوان Regression نده:

1. Web Bridge update باید Atomic بماند.
2. `owner_key` صفحه مدیریت در Update عوض نشود.
3. `agent_token` اتصال LlamaForge در Update عوض نشود.
4. Token rotation فقط با اقدام صریح کاربر.
5. `data/config.php` یا داده‌های Persistent توسط Update جایگزین نشوند.
6. SSE باید Long-poll fallback داشته باشد.
7. CPU/GPU manual modes حذف نشوند.
8. Adaptive نباید تنظیم دستی کاربر را نابود کند.
9. اگر Vulkan شکست خورد، fallback واضح و امن باشد.
10. اگر Full RAM/OOM شکست خورد، mmap fallback امن باشد.
11. مدل یا Attachment بدون نیاز دوباره در RAM کپی نشود.
12. هیچ Secret/Token داخل Log یا Repository قرار نگیرد.

---

# بخش G — تست‌هایی که باید اضافه شوند

فقط Unit Test ساده کافی نیست.

حداقل تست‌های جدید:

- Persian/English mixed intent routing
- date/time routing
- tomorrow calendar create
- local write with external write disabled
- URL read vs browser action
- arbitrary attachment acceptance
- metadata-only attachment path
- explicit file content read
- ZIP listing without eager extraction
- archive traversal protection
- duplicate tool-call recovery
- read-only parallel tool execution
- serialized writes
- streaming final answer
- cancel during stream
- SSE reconnect/dedup
- AutoTune cache hit
- AutoTune invalidation after model/runtime change
- best thread count not overwritten by heuristic
- batch/ubatch search
- large model safe profile
- lazy mmproj
- Vulkan failure fallback
- low-memory fallback
- Bridge token persistence

هر Bug واقعی که هنگام Audit پیدا شد، اول برایش Regression Test بنویس و بعد Fix کن.

---

# بخش H — روش کار مورد انتظار

ترتیب کار:

1. Baseline و اجرای تست‌ها
2. Architecture map
3. پیدا کردن Bug/Hotspot واقعی
4. Benchmark baseline
5. تغییرات کوچک و قابل اندازه‌گیری
6. Regression tests
7. Benchmark after
8. Refactor فقط وقتی دلیل واقعی دارد
9. Documentation update
10. گزارش نهایی

از Rewrite کامل پروژه بدون دلیل خودداری کن.

Dependency جدید فقط وقتی اضافه کن که مزیت روشن داشته باشد.

روی Windows و سخت‌افزار Low-RAM/iGPU حساس باش.

اگر چیزی فقط Usage را بالا می‌برد ولی tok/s را کم می‌کند، آن تغییر Optimization محسوب نمی‌شود.

---

# خروجی نهایی مورد انتظار از Codex/Astra

در پایان فقط نگوی «بررسی شد».

تحویل باید شامل این‌ها باشد:

- تغییرات واقعی کد
- لیست Bugهای پیدا شده
- دلیل هر تغییر مهم
- فایل‌های تغییرکرده
- تست‌های جدید
- نتیجه کامل Test Suite
- Benchmark قبل/بعد
- Prompt tok/s قبل/بعد
- Generation tok/s قبل/بعد
- TTFT قبل/بعد
- RAM قبل/بعد
- ریسک‌های باقی‌مانده
- مواردی که عمداً تغییر داده نشده‌اند
- پیشنهاد مرحله بعد

اگر نتیجه Benchmark نشان داد یک ایده سرعت را کم می‌کند، آن را Merge نکن.

**هدف نهایی LlamaForge این است که بدون نیاز به تنظیمات پیچیده از طرف کاربر، خودش بهترین مسیر Skill و بهترین پروفایل اجرای مدل را برای سخت‌افزار موجود پیدا کند؛ در عین حال کاربر حرفه‌ای همچنان کنترل دستی کامل داشته باشد.**
