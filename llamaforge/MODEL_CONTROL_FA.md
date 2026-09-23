# کنترل مدل و تنظیمات تولید — LlamaForge 0.23

## برنامه اصلی
Settings > Model & generation controls:
- Context limit: در بارگذاری بعدی مدل اعمال می‌شود و از سقف Context خود GGUF بیشتر نمی‌شود.
- Manual generation settings: اگر خاموش باشد Smart Chat مقادیر مناسب را خودکار تعیین می‌کند.
- Temperature / Top P / Top K / Min P / Repeat penalty / Max answer tokens: در درخواست بعدی اعمال می‌شوند و Reload لازم ندارند.
- Load / Reload / Unload: کنترل مستقیم llama-server از Settings.

## وب متصل
وب فقط فهرست امن مدل‌ها را با ID ناشناس دریافت می‌کند؛ مسیر فایل محلی ارسال نمی‌شود.
انتخاب مدل در بالای چت یک Model Load Request روی Bridge می‌سازد. LlamaForge حتی در حالت بدون مدل heartbeat را ادامه می‌دهد، درخواست را دریافت می‌کند، مدل فعلی را در صورت نیاز متوقف می‌کند و مدل انتخاب‌شده را با Context/Memory تنظیم‌شده در برنامه اصلی بالا می‌آورد.

تنظیمات Temperature و Sampling عمداً در وب نمایش داده نمی‌شوند و از برنامه اصلی کنترل می‌شوند.
