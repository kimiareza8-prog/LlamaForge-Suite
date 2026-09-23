# AI Bridge 3.8 — LlamaForge Chat UI

وب‌فرانت سبک PHP برای اتصال مستقیم به LlamaForge Local Agent، با تجربه کاربری نزدیک به ChatGPT.

## قابلیت‌ها

- انتخاب مدل محلی از وب و درخواست Load/Switch در LlamaForge
- توقف پاسخ جاری و امکان Stop/Unload مدل از وب
- تاریخچه گفتگو با Sidebar، حذف تک‌گفتگو و پاک‌کردن کل تاریخچه
- جداسازی تاریخچه بر اساس Browser Identity؛ هر مرورگر فقط تاریخچه خودش را می‌بیند
- بازیابی فهرست Sessionهای همان مرورگر از سرور
- ذخیره Draft هر گفتگو در مرورگر
- Live Agent Activity با Timeline جمع‌شونده
- Partial answer و Long-Poll بدون Refresh
- طراحی Responsive موبایل با Safe Area
- اتصال پس‌زمینه به LlamaForge از طریق `connect.php`

## نصب

PHP 7.4+ لازم است. پوشه `data` باید قابل نوشتن باشد. `data/config.php` در اولین اجرا با Secretهای تصادفی ساخته می‌شود و در `.gitignore` قرار دارد.

برای جزئیات نصب فایل `START-HERE.txt` را بخوان.
