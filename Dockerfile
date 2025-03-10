FROM python:3.10

# ✅ تثبيت Chrome و ChromeDriver
RUN apt-get update && apt-get install -y \
    chromium \
    chromium-driver \
    && rm -rf /var/lib/apt/lists/*

# ✅ ضبط المسارات الصحيحة
ENV CHROMIUM_PATH=/usr/bin/chromium
ENV CHROMEDRIVER_PATH=/usr/bin/chromedriver

# ✅ إنشاء مجلد للتطبيق
WORKDIR /app
COPY . /app

# ✅ تثبيت المتطلبات
RUN pip install --no-cache-dir -r requirements.txt

# ✅ تشغيل التطبيق
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000"]
