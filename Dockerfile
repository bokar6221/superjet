# استخدم صورة Python الرسمية
FROM python:3.10

# تثبيت Chrome و ChromeDriver
RUN apt-get update && apt-get install -y chromium chromium-driver

# ضبط متغيرات البيئة لـ Selenium
ENV CHROMIUM_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# تعيين مجلد العمل
WORKDIR /app

# نسخ جميع الملفات إلى الحاوية
COPY . .

# تثبيت المتطلبات
RUN pip install --no-cache-dir -r requirements.txt

# تشغيل التطبيق
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:8000", "app:app"]
