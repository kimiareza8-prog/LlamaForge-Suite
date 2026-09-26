# عیب‌یابی و توسعهٔ LlamaForge 0.34.1-stability

مبنای این دور، کد تحویلی 0.34.0 با ۳۳۴ تست موفق و یک تست PHP اجرا‌نشده است. Main تغییر نکرده بود؛ کار روی همان شاخهٔ اصلاحات ادامه یافت. گزارش اولیهٔ معماری، Skills و بنچمارک در `AUDIT_REPORT_FA.md` باقی است.

## خطاهای بازتولیدشده و اصلاح

| مشکل | نتیجهٔ اصلاح |
|---|---|
| handler خطای نصب Brain از متغیر تعریف‌نشدهٔ cancelled استفاده می‌کرد | خطای اصلی ثبت می‌شود و وضعیت error/cancelled منتشر می‌شود |
| روشن‌کردن مجدد Brain، لغو کار در حال اجرا را پاک می‌کرد | لغو برای همان کار قطعی می‌ماند؛ روشن‌شدن روی کار بعدی اثر می‌گذارد |
| پیش‌نمایش درس از لغو کار قبلی اثر می‌گرفت | preview مستقل است؛ آموزش فعال همچنان از Event لغو خودش استفاده می‌کند |
| شکست preflight می‌توانست یک مدل خاموش را خودکار Load کند | بازیابی Runtime فقط وقتی این کار آن را متوقف کرده باشد |
| reload بعد از آموزش از مسیر آخرین اجرای مدل استفاده می‌کرد | مسیر مدلِ آموزش‌دیده صریحاً به launch payload متصل می‌شود |
| تغییر مدل در compilation/preflight می‌توانست کار قدیمی را ادامه دهد | بررسی مدل در مرزهای compilation، unload، progress، reload و confirmation؛ عدم بازگرداندن مدل قدیمی روی انتخاب جدید |
| آماده‌سازی/دانلود/doctor پس‌زمینه کادر چت را غیرفعال می‌کرد | یک تابع مشترک وضعیت setup/learning/pending را تعیین می‌کند؛ چتِ مدل آماده قابل استفاده است |
| بازسازی composer ممکن بود قفل strict-learning را بردارد | pending lesson هنگام render نیز حفظ می‌شود |
| calendar.update فیلد time/Jalali را نادیده می‌گرفت؛ start جدید ممکن بود با end قبلی نامعتبر شود | تغییر ساعت/تاریخ و حفظ مدت جلسه، مگر اینکه end صریح باشد |
| all_day بدون end تنها یک ساعت زمان می‌گرفت | مدت پیش‌فرض ۲۴ ساعت |
| تاریخ/ساعت خالی در update بی‌صدا نادیده گرفته می‌شد | خطای اعتبارسنجی؛ رویداد ذخیره‌شده تغییر نمی‌کند |
| پاسخ دیرهنگام تبدیل تاریخ می‌توانست متن جدیدِ در حال تایپ را پاک کند | تایپ جدید، درخواست قدیمی را نامعتبر می‌کند |

۱۸ سناریوی Backend و ۸ سناریوی اجرایی UI به تست‌ها اضافه شدند. دسته‌های خطا ابتدا با تست قرمز ثبت و سپس اصلاح شدند؛ نتایج در `audit/followup-*.txt` هستند. برای fixture قدیمیِ مدل نیز فیلد path واقعی اضافه شد؛ مدل واقعی همیشه این فیلد را دارد.

## قابلیت جدید

در تقویم محلی، جستجوی عنوان، مکان و یادداشت در همهٔ ماه‌ها اضافه شد. نتیجه مستقیماً فرم ویرایش همان رویداد را باز می‌کند. ورودی ۲۵۰ms debounce دارد؛ پاسخ قدیمی نمی‌تواند نتایج جستجوی جدید را جایگزین کند. عنوان‌ها escape می‌شوند. هر جستجو حداکثر ۵۰ نتیجه دارد و رسیدن به سقف در UI اعلام می‌شود. این قابلیت از عملیات عمومی موجود تقویم استفاده می‌کند؛ Skill تازه برای جمله‌های خاص اضافه نشده است.

## اعتبارسنجی

- مجموعهٔ کامل: **352 passed، 1 skipped، 57.68s**.
- ۱۴ آزمون اجرایی Node برای حالت‌های UI، شامل ۸ سناریوی این دور؛ در pytest نیز اجرا می‌شوند.
- بررسی syntax JavaScript محلی و Bridge و `git diff --check`.
- کد PHP، توکن‌ها، updater اتمی، SSE/long-poll و CSS میزبانی‌شده در این دور تغییر نکردند.
- Dependency اجباری تازه و کپی اضافهٔ مدل ایجاد نشد.

## محدودیت‌ها و مرحلهٔ بعد

این دور بهبود پایداری و قابلیت تقویم است؛ بهبود tok/s ادعا نمی‌شود. Prompt tok/s، Generation tok/s، TTFT، Load time و Peak RAM مدل واقعی در این محیط اندازه‌گیری نشده‌اند. تست‌های UI از DOM شبیه‌سازی‌شده استفاده می‌کنند؛ محدودیت مرورگر برای preview محلی و نبود Windows/iGPU و PHP همچنان برقرار است.

بررسی مدل در نقاط حساس، جای scheduler کامل مالکیت Runtime را نمی‌گیرد. تغییرات هم‌زمان همهٔ مسیرهای remote/setup/learn و هم‌پوشانی کوتاه حافظهٔ trainer و بارگذاری مدل هنوز به ارزیابی گسترده‌تر نیاز دارند. مدل‌های هم‌نام با معماری/size_label یکسان همچنان به بازطراحیِ هویت profile با مهاجرت امن احتیاج دارند. این دور profileهای قبلی را مهاجرت یا حذف نکرد.

اولویت بعدی: تست end-to-end روی دستگاه Windows کاربر با یک GGUF ثابت، بررسی تصویری تقویم در موبایل، و ارزیابی مستقل از دادهٔ آموزش برای تشخیص فراموشی/خطای یادآوری Brain. جستجو و فرم ویرایش جدید تقویم محلی هنوز به Web Bridge منتقل نشده‌اند.

## فایل‌های این دور

- `.gitignore`
- `README.md`
- `README_FA.md`
- `VERSION`
- `llamaforge/BRAIN_SYSTEM_FA.md`
- `llamaforge/CHANGELOG.md`
- `llamaforge/FOLLOWUP_REPORT_FA.md`
- `llamaforge/SKILL_SYSTEM_FA.md`
- `llamaforge/VERSION`
- `llamaforge/audit/followup-model-before.txt`
- `llamaforge/audit/followup-regression-before.txt`
- `llamaforge/audit/followup-regression-round2.txt`
- `llamaforge/audit/followup-strict-before.txt`
- `llamaforge/audit/followup-ui-before.txt`
- `llamaforge/audit/test-suite-0.34.1.txt`
- `llamaforge/audit/ui-tests-0.34.1.txt`
- `llamaforge/llamaforge/__init__.py`
- `llamaforge/llamaforge/core/net.py`
- `llamaforge/llamaforge/core/personal_brain.py`
- `llamaforge/llamaforge/core/trainable_models.py`
- `llamaforge/llamaforge/core/workspace.py`
- `llamaforge/llamaforge/web/server.py`
- `llamaforge/llamaforge/web/static/app.js`
- `llamaforge/llamaforge/web/static/macos.css`
- `llamaforge/llamaforge/web/static/stream_protocol.js`
- `llamaforge/tests/test_brain_rewrite.py`
- `llamaforge/tests/test_stability_followup.py`
- `llamaforge/tests/test_web_shell.py`
- `llamaforge/tests/test_workspace_ui.cjs`
- `versions/0.34.1-stability/LlamaForge-Suite-0.34.1-stability.zip`
- `versions/0.34.1-stability/SHA256SUMS`
