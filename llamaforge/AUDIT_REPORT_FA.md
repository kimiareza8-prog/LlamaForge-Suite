# گزارش فنی LlamaForge Suite 0.34.0-smart-brain

مبنای تغییر: نسخهٔ 0.33.0-adaptive-engine، commit `889ca92c2dccdccd173e415734c53c09ebb6f6ac`.
این تحویل شامل اصلاح Smart Skills، محافظت‌های Adaptive، بازنویسی جریان آموزش Brain و بازطراحی چت و تقویم مطابق درخواست‌های بعدی است. هیچ بهبود tok/s روی سخت‌افزار واقعی ادعا نمی‌شود.

## نتیجهٔ تست

- Baseline پیش از تغییر: **189 passed، 40.75 ثانیه**؛ پس از نصب وابستگی‌های اختیاری تست Torch/Safetensors.
- نتیجهٔ نسخهٔ نهایی: **334 passed، 1 skipped، 56.74 ثانیه**.
- `node --check` برای JS برنامه و Bridge؛ تست اجرایی Node برای SSE split/dedup، reconnect cursor، حفظ Context draft و ۶ سناریوی جریان UI؛ اسکریپت‌های Node در مجموعهٔ pytest نیز اجرا می‌شوند.
- تست PHP حفظ توکن و پیکربندی آماده است؛ PHP CLI در محیط حاضر نصب نیست. کد اجرایی Bridge در این نسخه تغییری ندارد؛ فقط CSS تغییر کرده است.
- 40 شکست اولیه در اولین دستهٔ 58 تست Skills ثبت شد؛ هر دستهٔ اصلاح بعدی نیز ابتدا با تست قرمز ثبت شد. فایل‌های `audit/*-red.txt` و `audit/skills-before.txt` شواهد بازتولید را نگه می‌دارند.
- مدل‌های تست کنترل‌شده‌اند؛ تست فارسی/انگلیسی/مخلوط، صحت مسیر و قرارداد را می‌سنجد، نه درصد دقت یک GGUF واقعی.

## نقشهٔ معماری و محل کنترل

| بخش | مسئولیت |
|---|---|
| skill_system.py | Familyها، کاتالوگ، Shortlist، Guardهای واضح و Preflight |
| skill_contracts.py | نوع عملیات، مجوز، Schema، پیش‌نیاز، Side Effect، Parallel Safety و Verify |
| agent_engine.py | Router/Planner، Observation، تغییر برنامه، رسید و جلوگیری از تکرار |
| agent_tools.py | اجرای ابزارها، اتصال به HTTP/Browser و Verify نوشتن‌های محلی |
| workspace.py / archive_reader.py | تقویم، فایل، رسید Attachment، کنترل مسیر و خواندن محدود |
| planner.py / autotune.py | برنامهٔ حافظه/اجرا و جستجوی سنجیدهٔ پارامترهای اجرا |
| runtime.py / hardware.py | Runtime فعال، پیدا کردن binary هم‌نسخه و شناسهٔ سخت‌افزار/Driver |
| server.py / net.py | یک llama-server مشترک، بارگذاری Vision هنگام نیاز، Stream و لغو |
| learning_data.py / training_loop.py | قرارداد مثال آموزشی، curriculum اصلاحات و replay، ارزیابی محدود و گام‌های optimizer |
| personal_brain.py / trainer_worker.py | آموزش قابل لغو، کنترل حافظه، تبدیل و فعال‌سازی تراکنشی adapter |
| app.js / stream_protocol.js / workspace_ui.js | درخت عملیات، Saved/Active Context، دریافت Stream، پیش‌نویس چت و تقویم |

مدل از روی هدف Family را انتخاب می‌کند. رتبه‌بندی واژه‌ها فقط پس از این مرحله و در مجموعهٔ محدود ابزارهای همان خانواده انجام می‌شود. هیچ Skill مخصوص یک جمله یا درخواست خاص ساخته نشده است. پیام روشن و ساده مانند سلام، inference جداگانهٔ Router ندارد.

## خطاهای واقعی و اصلاح

| خطای بازتولیدشده | اصلاح و اثر |
|---|---|
| Calendar manifest ناقص و operation نامعتبر | Schema کاملِ ابزارهای منتخب، Enum و پیش‌نیازهای هر عملیات پیش از اجرا |
| `Invalid isoformat string: 10:00` | ترکیب date/time یا Jalali/time یا relative_date/time؛ ساعت بدون تاریخ خطای راهنمای روشن می‌دهد |
| Guard بر اساس صرفِ ذکر meeting/tomorrow/browser | تمایز جملهٔ خبری/توضیح/ترجمه از درخواست عملیاتی؛ مدل همچنان تصمیم اصلی را دارد |
| ابزارهای نامرتبط در Shortlist تقویم | محدودکردن Family پیش از رتبه‌بندی؛ تقویم ۵ ابزار → ۱ ابزار |
| inference اضافی Router/Family | سلام بدون Router؛ Family در همان پاسخ Router در صورت پشتیبانی مدل |
| انتقال Attachment، شناسهٔ قدیمی را نامعتبر می‌کرد | رسید پایدار جدا از Blob، اتصال رسید به File ID و حفظ آن در تاریخچهٔ مرورگر |
| تکرار تاریخچه، Attachment را چند بار کپی می‌کرد | Dedup محتوا+نام در Scope همان Workspace |
| Path traversal / symlink escape / listing پوشهٔ ناموجود | اعتبارسنجی مسیر مقصد و محدوده؛ list دیگر mkdir ضمنی نمی‌کند |
| خواندن غیرایمن ZIP/Office و sharedStrings نامرتب XLSX | محدودیت اندازه/تعداد/ratio، جلوگیری از لینک و مسیر خطرناک، خواندن انتخابی ZIP/TAR، حل sharedStrings |
| JSON حاوی تصویر به‌صورت خام بریده می‌شد | محدودکردن محتوا در Reader و Observation؛ Envelope معتبر باقی می‌ماند |
| تصویر خوانده‌شده در Final پس از پایان بودجه گم می‌شد | انتقال صریح تصویر به پیام Final نیز انجام می‌شود |
| Scope صاحب فایل در Threadهای موازی گم می‌شد | انتقال و بازیابی Scope در هر Worker |
| Browser session یا Download به‌اشتباه read-only موازی بود | سیاست واقعی Operation؛ Browser سری، Download نوشتن محلی |
| تکرار نوشتن موفق یا خطای Preflight | رسید همان Turn بازاستفاده می‌شود؛ فراخوانی شکست‌خوردهٔ عیناً تکراری مسدود می‌شود |
| نتیجهٔ local write پیش از تأیید ذخیره‌شدن گزارش می‌شد | بررسی ID و وضعیت ذخیره‌شده پس از عملیات |
| لغو، منتظر ThreadPool خواندن می‌ماند | لغو بدون join اجباری، Deadline برای خواندن موازی؛ I/O فعال تابع Timeout خودش |
| خواندن/ذخیرهٔ عکس بی‌دلیل mmproj را Load می‌کرد | Agent ابتدا Metadata؛ بارگذاری projector تنها هنگام ورود image_url واقعی به مدل |
| Final در مسیر پایان بودجه/خطای Planner buffered بود | هر مسیر Final از Stream واقعی استفاده می‌کند |
| چند نویسهٔ نخست Stream نگه داشته می‌شد | فقط prefix احتمالی تگ reasoning نگه داشته می‌شود |
| قطع Stream، malformed JSON یا خطای backend موفقیت فرض می‌شد | خطای صریح و بررسی Final marker؛ فقط یک HTTP header و marker نهایی خطا |
| SSE وضعیت replay/long-poll نداشت | تاریخچهٔ bounded، ترتیب زیر Lock، Last-Event-ID، resync و Long-poll |
| Cancel مرورگر به backend نمی‌رسید | Request ID، endpoint لغو، بررسی توقف ایجنت و بستن socket فعال inference |
| ویرایش خالی/ناتمام Context بازنویسی می‌شد | نگهداری draft خام و اعتبارسنجی هنگام Save؛ نمایش Saved و Active |
| API token داخل خطای Log دیده می‌شد | Redaction در مرز Log و Progress، بدون تغییر ورودی واقعی ابزار |
| Adaptive روی CPU محافظت حافظه را از دست می‌داد | Adaptive مستقل از وجود GPU باقی می‌ماند؛ مؤثر: GPU layers صفر |
| Thread دستی با heuristic یا cache بازنویسی می‌شد | Thread policy دستی حفظ می‌شود؛ benchmark فقط Auto را تعیین می‌کند |
| حافظه/Context چند slot اشتباه حساب می‌شد | KV و Context هر slot لحاظ می‌شود؛ پیش‌فرض یک slot تا شواهد واقعی |
| AutoTune یک اجرای کوتاه و Batch غیرواقعی داشت | جستجوی مرحله‌ای، پرامپت ۵۱۲، سه تکرار، Median و جریمهٔ ناپایداری |
| خرابی یک Candidate کل AutoTune را از بین می‌برد | خطای هر Candidate ثبت و CPU قابل مقایسه حفظ می‌شود؛ لغو/Timeout در همهٔ مراحل |
| Cache فقط به مسیر/اندازه/mtime مدل متکی بود | مدل+quant، Runtime/library identity، CPU/GPU/Driver/RAM و Context/load profile |
| Custom server با llama-bench نسخه‌ای دیگر سنجیده می‌شد | bench باید کنار همان custom binary باشد |
| Speculative صرفاً با وجود Flag روشن می‌شد | Auto خاموش تا سنجش end-to-end؛ انتخاب صریح N-gram باقی است |
| Vulkan startup failure | یک تلاش محدود CPU؛ OOM Full RAM همچنان یک fallback mmap دارد |

## بازنویسی Brain

قرارداد داده و حلقهٔ یادگیری از مدیریت دانلود/فرایند جدا شد. راهنمای کامل: `BRAIN_SYSTEM_FA.md`.

| خطای واقعی | اصلاح |
|---|---|
| پرسش و سلام به‌عنوان حقیقت هویتی یاد گرفته می‌شد | تشخیص پرسش پیش از Teacher، نمونه‌سازی قطعی محدود و اعتبارسنجی evidence |
| پاسخ Teacher بدون شاهد وارد آموزش می‌شد | پاسخ خودکار باید در نقل‌قولی از پیام فعلی کاربر شاهد داشته باشد؛ آموزش صریح سؤال/جواب از خود کاربر |
| replay=0 با مقدار پیش‌فرض جایگزین می‌شد | صفر حفظ می‌شود؛ تاریخچهٔ پاسخ‌های منسوخ کنار گذاشته می‌شود |
| در سه micro-step نمونه‌های replay سهمی نداشتند | برنامهٔ new → replay → new و حذف تعارض هویت بین دو زبان |
| reload زنده، candidate خودش را rollback می‌کرد | مالکیت pending reload؛ تأیید نسل فقط پس از reload موفق؛ بازیابی crash |
| همهٔ encoded batch به دستگاه منتقل می‌شد | انتقال micro-batch مورد استفاده، کنترل loss/gradient غیرمتناهی |
| Labelهای پاسخ ممکن بود مرز قالب را بشکنند | برش پاسخ بر اساس مرز دقیق قالب و رد قالب ناسازگار |
| خروجی‌های بدون خط stdout می‌توانستند نامحدود معطل بمانند | Deadline مستقل برای subprocess، لغو و جمع‌کردن فرایند |
| نام 7B به جای اندازهٔ واقعی checkpoint معیار حافظه بود | guard محافظه‌کارانه بر اساس اندازه، load mode، طول و rank |
| حالت پایان Brain کادر چت را قفل نگه می‌داشت | آزادشدن composer در done/error/cancelled |

Ledger تأییدشده با پنجرهٔ محدود replay خوانده می‌شود؛ درس تکراری جدید بی‌دلیل آموزش نمی‌بیند. هیچ تغییر وزن تأییدنشده‌ای در شمارندهٔ نسل موفق منظور نمی‌شود. معیار loss روی curriculum آموزش است، نه ارزیابی مستقل دانایی یا تضمین حفظ همهٔ دانسته‌های مدل.

## رابط چت و تقویم

رابط محلی با الگوی چت ساده، نوار کناری خلوت، ابزارهای فرعی در منوی بازشونده، جستجو/تغییر نام تاریخچه، کادر پیام گرد، خوانایی فارسی و چیدمان موبایل بازطراحی شد. جزئیات Agent به‌صورت پیش‌فرض جمع است و قابل بازکردن است. حالت‌های CPU/GPU/Hybrid/Max Both/Adaptive و تنظیمات دستی در دسترس باقی‌اند.

تقویم محلی اکنون نمای ماهانه، برنامهٔ روز انتخاب‌شده، ساخت/ویرایش/لغو قرار، تبدیل تاریخ شمسی/میلادی، تمام‌روز، مکان و یادداشت دارد. ساعت میزبان در فرم مشخص است؛ تاریخ/ساعتِ واردشده دیگر با timezone مرورگر جابه‌جا نمی‌شود. ارسال دوبارهٔ فرم تا پایان ذخیره غیرفعال است؛ خطا در همان فرم نمایش داده می‌شود. رویداد چندروزه تا پایان exclusive نمایش داده می‌شود. محدودیت نمایش ماه ۵۰۰ رویداد است و در رسیدن به سقف، UI آن را اعلام می‌کند.

خطاهای جریان چت نیز با تست قرمز بازتولید و رفع شدند: ازبین‌رفتن پیش‌نویس هنگام افزودن فایل یا بازکردن تنظیمات، ادامهٔ stream پس از تغییر گفتگو، حذف پاسخ جدید توسط پایان دیرهنگام درخواست لغوشده، و انتقال فایل دوم به گفتگوی اشتباه هنگام تعویض گفتگو. پیش‌نویس‌های ارسال‌نشده در حافظهٔ همان صفحه و جدا برای هر گفتگو می‌مانند؛ refresh صفحه تضمین بازیابی آن‌ها را ندارد.

در Web Bridge میزبانی‌شده فقط `assets/app.css` تغییر کرد: چت روشن و خلوت، تقویم خوانا و فرم ثبت سریع با اندازهٔ مناسب. امکانات جدید فرم ویرایش تقویمِ محلی هنوز به Bridge منتقل نشده‌اند.

آزمون UI شامل اجرای منطق واقعی `app.js` با DOM شبیه‌سازی‌شده در Node است؛ معادل تست تصویری مرورگر نیست. پیش‌نمایش localhost و file در مرورگر این محیط به‌وسیلهٔ سیاست دسترسی مسدود شد. بنابراین screenshot یا تأیید ظاهری روی Windows/Mobile ادعا نمی‌شود.

## بنچمارک قبل / بعد

اسکریپت `scripts/benchmark_skills.py` روی کد اصلی commit مبنا و کد اصلاح‌شده اجرا شد. ۷ اجرای جدا، هرکدام ۲۵ Shortlist و ۲۰ staging؛ جدول میانهٔ اجرای آن‌هاست. کاتالوگ داخل هر درخواست بازاستفاده می‌شود. این Microbenchmark مسیر کنترل است و مدل آن Stub است.

| معیار | قبل | بعد |
|---|---:|---:|
| فراخوانی مدل برای سلام | ۲ | ۱ |
| تعداد ابزار در Shortlist تقویم | ۵ | ۱ |
| میانهٔ زمان Shortlist+Manifest | 0.7793 ms | 0.0924 ms |
| نویسه‌های Manifest تقویم | 2493 | 2070 |
| شناسهٔ یکتا برای ۲۰ بار همان Attachment | ۲۰ | ۱ |
| فضای Inbox | 2,005,040 bytes | 100,371 bytes |
| زمان staging در ۲۰ Turn | 4.243 ms | 3.724 ms |

کاهش اصلی قابل اتکا: یک inference کمتر برای سلام، Shortlist کوچک‌تر و حدود ۹۵٪ فضای کمتر برای Attachment تکراری. تفاوت چند میلی‌ثانیه‌ای staging به Cache سیستم‌عامل حساس است؛ سرعت تک‌آزمون همیشه بهتر نبود. نتیجه به‌عنوان افزایش tok/s گزارش نمی‌شود.

| معیار واقعی inference | قبل | بعد | وضعیت |
|---|---|---|---|
| Prompt tok/s | — | — | GGUF و llama-bench در محیط حاضر نیست |
| Generation tok/s | — | — | اندازه‌گیری نشده |
| TTFT واقعی | — | — | اندازه‌گیری نشده؛ مقدار AutoTune یک برآورد از محاسبات است |
| Peak RAM مدل | — | — | اندازه‌گیری نشده؛ فضای Inbox جایگزین RAM نیست |
| Load time مدل | — | — | اندازه‌گیری نشده |
| Shared GPU memory | — | — | Windows/iGPU هدف در دسترس نیست |

AutoTune اکنون Thread → GPU layers → Batch/UBatch → بازبینی Thread اطراف برنده را انجام می‌دهد؛ Cartesian Product بزرگ ندارد. با دادهٔ موجود Interactive/Throughput امتیاز متفاوت دارند؛ Fit Largest از RAM اندازه‌گیری‌شده در صورت دسترسی استفاده می‌کند. گزینهٔ UI ساده باقی مانده است. شاهد فنی ویژگی‌های llama-bench: https://github.com/ggml-org/llama.cpp/blob/master/tools/llama-bench/README.md . این ابزار زمان tokenization/sampling و مسیر speculative سرور را اندازه نمی‌گیرد.

### سنجش Brain

۷ تکرار، هرکدام ۱۰۰۰ فراخوانی compiler؛ زمان میانهٔ baseline برابر 0.002933ms و بعد 0.002889ms بود. اختلاف چند میکروثانیه‌ای شاهد بهبود سرعت کلی مدل نیست.

| سناریوی کنترل‌شده | قبل | بعد |
|---|---:|---:|
| سؤال هویتی که اشتباهاً آموزش می‌دید، از ۷ نمونه | ۷ | ۰ |
| پاسخ هویتی منسوخ در curriculum نمونه | ۴ | ۰ |
| نمونهٔ replay وقتی خاموش است | ۱ | ۰ |
| گام replay در بودجهٔ ۳ گام | ۰ | ۱ |

یک مدل کوچک واقعی PyTorch با ۴ پارامتر و سه گام اجرا شد: loss از 0.693147 به 0.267507 رسید و وزن‌های frozen ثابت ماندند. این فقط sanity check حلقهٔ optimizer است؛ کیفیت آموزش Transformers/LoRA، GGUF و یادآوری فارسی روی مدل واقعی سنجیده نشده است. ارزیابی bounded می‌تواند تا ۱۶ forward اضافه داشته باشد؛ کاهش زمان کلی آموزش ادعا نمی‌شود.

## مرزهای حفظ‌شده

کد اجرایی PHP/JavaScript و به‌روزرسانی `web-bridge/` تغییر نکرده است؛ تنها فایل CSS ظاهر آن اصلاح شد. Atomic updater، owner_key، agent_token، عدم جایگزینی data/config.php، مسیر Rotate صریح و Long-poll میزبانی همان نسخهٔ مبنا هستند. فایل ZIP نهایی شامل secret runtime، config.php تولیدشده، Model، cache یا browser profile نیست. CPU/GPU/Hybrid/Max Both و کنترل دستی باقی مانده‌اند. Context ذخیره‌شدهٔ کاربر به خاطر clamp اجرایی تغییر نمی‌کند.

## ریسک‌های باقی‌مانده / موارد عمداً تغییرنداده‌شده

1. آزمون واقعی روی Windows با 16GB RAM و Intel HD 530 انجام نشده؛ fallback با Stub فرایند سنجیده شده است. اگر خود binary Vulkan بدون Driver حتی در حالت CPU شروع نشود، انتخاب Runtime CPU لازم می‌شود.
2. بارگذاری نخستین projector، شکست آن و رقابت هم‌زمان model switch/vision هنوز به آزمون سخت‌افزاری نیاز دارند. projector پس از استفاده برای ادامهٔ همان مدل گرم می‌ماند؛ متن اولیه آن را بارگذاری نمی‌کند. سقف تصویر هر تصمیم/Final فعلاً دو تصویر است.
3. برآورد KV هنوز مدل‌محورِ دقیق بر اساس geometry همهٔ GGUFها نیست. Slots بیشتر، threads-batch مستقل، KV type، context depth و load-mode به‌عنوان محور کامل جستجوی واقعی اضافه نشده‌اند؛ پارامترهای بدون شاهد خودکار فعال نشدند.
4. Speculative/draft model سنجیده نشده و Auto خاموش است. هیچ draft model اضافه در RAM بارگذاری نمی‌شود.
5. یک مدل مشترک باقی است. قفل کلی گردش کار Remote Agent و نبود scheduler با fairness کامل عمداً بازنویسی نشد؛ یک درخواست بلند هنوز می‌تواند منتظر ماندن درخواست دیگر را زیاد کند.
6. Cancel پاسخ HTTP فعال را می‌بندد؛ انتظار قبل از دریافت header و کتابخانه‌های خارجیِ فاقد لغو هنوز محدود به Timeout خودشان هستند. Threadهای خواندن با زور کشته نمی‌شوند.
7. SSE replay محلی برای وضعیت است؛ Resume خودکار POST چت قطع‌شده پیاده‌سازی نشده تا عمل نوشتن ناخواسته تکرار نشود. Dedup رویداد بر اساس ID است، نه یکسان‌بودن متن توکن‌ها.
8. fingerprint مدل شامل stat و Hash نمونهٔ header/tail است، نه Hash کامل فایل چندگیگابایتی در هر بار status. تغییر عمدی وسط فایل همراه با حفظ stat می‌تواند از این نمونه‌برداری عبور کند.
9. Verify خارجی به امکان read/snapshot سرویس وابسته است؛ تضمین تراکنش یا exactly-once عمومی نداریم. Parse نبودن PDF/Audio/Video/Unknown، دریافت Metadata را متوقف نمی‌کند.
10. Snapshot فایل قبل از تغییر، کامل اعتبارسنجی می‌شود؛ خرابی دیسک در میانهٔ جایگزینی همچنان نیازمند پشتیبان است. atomic بودن Bridge با snapshot فایل یکسان نیست.
11. Redaction الگوهای شناخته‌شدهٔ credential را حذف می‌کند؛ جایگزین حفاظت از secret بدون نام/ساختار در متن آزاد نیست.
12. UI از نظر منطق و syntax تست شد؛ تست تصویری روی Windows/mobile و اجرای PHP در این محیط انجام نشده است. کد اجرایی جدید Dependency اجباری تازه ندارد.
13. در Brain، بررسی loss روی دادهٔ آموزش جای held-out semantic evaluation را نمی‌گیرد. profile identity قدیمی و رقابت تمام مسیرهای remote model switch/learning هنوز به بازطراحی کامل نرسیده‌اند؛ نسخهٔ ابزار تبدیل/وابستگی‌های trainer نیز کاملاً pin نشده است.
14. زمان پیش‌فرض و تعطیلات تقویم بر اساس timezone میزبان و مجموعهٔ موجود تعطیلات ثابت است؛ تقویم کامل تعطیلات قمری/رسمی افزوده نشده است. Reminder ذخیره می‌شود؛ ارسال اعلان وقتی برنامه بسته است در این کار اضافه نشده است.

## مرحلهٔ بعد

روی همان دستگاه هدف و یک GGUF ثابت، ابتدا نسخهٔ مبنا و سپس این نسخه را با Context یکسان اجرا کنید؛ ۱ warm-up و حداقل ۳ تکرار با prompt کوتاه/بلند. Prompt tok/s، Generation tok/s، TTFT واقعی، زمان Load و Peak RAM را ثبت کنید. سپس slot/KV/speculation را تک‌محور و end-to-end مقایسه کنید؛ گزینه‌ای که سرعت یا پایداری را بدتر می‌کند به حالت خودکار اضافه نشود. برای دقت نیز مجموعهٔ درخواست‌های واقعی فارسی و mixed را روی مدل انتخابی اجرا کنید. برای توسعهٔ Brain، اولویت بعدی یک مجموعهٔ ارزیابی مستقل از دادهٔ آموزش (یادآوری، اصلاح، فراموشی و فارسی)، صف درس‌های قابل بازبینی، و اجرای آموزش دسته‌ای در زمان بیکاری است؛ یادگیری در هر نوبت بدون سنجش مستقل دوباره فعال نشود.

## فایل‌های تغییرکرده

- `README.md`
- `README_FA.md`
- `VERSION`
- `llamaforge/AGENT_SKILLS_FA.md`
- `llamaforge/AUDIT_REPORT_FA.md`
- `llamaforge/BRAIN_SYSTEM_FA.md`
- `llamaforge/CHANGELOG.md`
- `llamaforge/README.md`
- `llamaforge/SKILL_SYSTEM_FA.md`
- `llamaforge/VERSION`
- `llamaforge/bridge_payload/web-bridge/PROFESSIONAL_UI_FA.md`
- `llamaforge/bridge_payload/web-bridge/assets/app.css`
- `llamaforge/llamaforge/__init__.py`
- `llamaforge/llamaforge/core/agent_engine.py`
- `llamaforge/llamaforge/core/agent_tools.py`
- `llamaforge/llamaforge/core/archive_reader.py`
- `llamaforge/llamaforge/core/autotune.py`
- `llamaforge/llamaforge/core/hardware.py`
- `llamaforge/llamaforge/core/learning_data.py`
- `llamaforge/llamaforge/core/net.py`
- `llamaforge/llamaforge/core/personal_brain.py`
- `llamaforge/llamaforge/core/planner.py`
- `llamaforge/llamaforge/core/redaction.py`
- `llamaforge/llamaforge/core/runtime.py`
- `llamaforge/llamaforge/core/skill_contracts.py`
- `llamaforge/llamaforge/core/skill_system.py`
- `llamaforge/llamaforge/core/trainable_models.py`
- `llamaforge/llamaforge/core/training_loop.py`
- `llamaforge/llamaforge/core/workspace.py`
- `llamaforge/llamaforge/trainer_worker.py`
- `llamaforge/llamaforge/web/server.py`
- `llamaforge/llamaforge/web/static/app.js`
- `llamaforge/llamaforge/web/static/index.html`
- `llamaforge/llamaforge/web/static/macos.css`
- `llamaforge/llamaforge/web/static/stream_protocol.js`
- `llamaforge/llamaforge/web/static/styles.css`
- `llamaforge/llamaforge/web/static/workspace_ui.js`
- `llamaforge/scripts/benchmark_brain.py`
- `llamaforge/scripts/benchmark_skills.py`
- `llamaforge/tests/test_adaptive_audit.py`
- `llamaforge/tests/test_agent_tools.py`
- `llamaforge/tests/test_brain_rewrite.py`
- `llamaforge/tests/test_bridge_identity.py`
- `llamaforge/tests/test_calendar_ui.py`
- `llamaforge/tests/test_secrets_audit.py`
- `llamaforge/tests/test_skill_audit.py`
- `llamaforge/tests/test_smart_skills_v4.py`
- `llamaforge/tests/test_stream_audit.py`
- `llamaforge/tests/test_web_protocol.cjs`
- `llamaforge/tests/test_web_protocol.py`
- `llamaforge/tests/test_web_shell.py`
- `llamaforge/tests/test_workspace_ui.cjs`
- `web-bridge/PROFESSIONAL_UI_FA.md`
- `web-bridge/assets/app.css`

شواهد خام آزمون‌ها و بنچمارک‌ها: `llamaforge/audit/`. بستهٔ انتشار و SHA256 نیز در `versions/0.34.0-smart-brain/` قرار می‌گیرند.
