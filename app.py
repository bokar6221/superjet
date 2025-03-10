import os
import time
import threading
import traceback
from flask import Flask, render_template, request, jsonify

app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ تعطيل Selenium عند الحاجة لتقليل استهلاك الموارد
USE_SELENIUM = os.getenv("USE_SELENIUM", "true").lower() == "true"

if USE_SELENIUM:
    from selenium import webdriver
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    def init_driver():
        options = Options()
        options.add_argument("--headless=new")  # استخدام `headless` الجديد لتجنب الأعطال
        options.add_argument("--no-sandbox")  # تعطيل `sandbox` لمنع الأعطال
        options.add_argument("--disable-dev-shm-usage")  # استخدام التخزين الفعلي بدلاً من `/dev/shm`
        options.add_argument("--disable-gpu")  # تعطيل `GPU` لتجنب الأعطال
        options.add_argument("--disable-software-rasterizer")  # تعطيل معالجة `GPU`
        options.add_argument("--disable-features=VizDisplayCompositor")  # منع العمليات غير المدعومة
        options.add_argument("--disable-blink-features=AutomationControlled")  # إخفاء Selenium عن مواقع الويب
        options.add_argument("--window-size=1280,1024")  # ضبط حجم النافذة الافتراضي
        options.add_argument("--remote-debugging-port=9222")  # إتاحة التصحيح في الخلفية
        options.add_argument("--single-process")  # تشغيل `Chrome` كعملية واحدة لتقليل استهلاك الموارد
        options.add_argument("--no-zygote")  # تعطيل عمليات `Zygote` التي تستهلك موارد كبيرة
        options.add_argument("--disable-crash-reporter")  # تعطيل إرسال تقارير الأعطال
        options.add_argument("--disable-extensions")  # تعطيل الإضافات لتسريع التحميل
        options.add_argument("--disable-background-networking")  # تقليل تحميل الشبكة
        options.add_argument("--disable-background-timer-throttling")  # تقليل تحميل النظام

        # ✅ تحديد موقع `Chromium`
        options.binary_location = "/usr/bin/chromium"

        # ✅ تشغيل `ChromeDriver`
        service = Service("/usr/bin/chromedriver")

        return webdriver.Chrome(service=service, options=options)

    def start_driver():
        global driver
        driver = init_driver()

    # ✅ تشغيل `Selenium` في `Thread` مستقل لمنع التعارض مع `Gunicorn`
    selenium_thread = threading.Thread(target=start_driver)
    selenium_thread.start()

    def do_login():
        """تنفيذ تسجيل الدخول تلقائيًا بعد التأكد من تحميل الصفحة."""
        try:
            driver.get("https://office.businmay.net/")

            # ✅ الانتظار حتى يظهر العنصر قبل محاولة التفاعل معه
            WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "office_code")))

            driver.execute_script("document.getElementById('office_code').value = '7';")
            driver.execute_script("onCodeChanged('office_id', '7');")
            driver.execute_script("document.getElementById('email').value = 'mahmod.h';")
            driver.execute_script("document.getElementById('password').value = '123';")

            # ✅ البحث عن زر تسجيل الدخول وإعادة المحاولة إذا كان `stale`
            for _ in range(5):  # تجربة 5 مرات في حالة `stale` أو `tab crashed`
                try:
                    login_btn = WebDriverWait(driver, 10).until(
                        EC.element_to_be_clickable((By.XPATH, "//button[text()='تسجيل دخول']"))
                    )
                    time.sleep(1)  # ✅ الانتظار لضمان تحميل العنصر بالكامل
                    login_btn.click()
                    break  # إذا نجح النقر، لا نحتاج لإعادة المحاولة
                except Exception as e:
                    print(f"⚠️ إعادة محاولة النقر على الزر: {e}")
                    time.sleep(2)  # ✅ انتظار أطول لمحاولة تحميل العنصر مجددًا
        
            time.sleep(3)  # ✅ انتظار تحميل الصفحة بعد تسجيل الدخول
            print("✅ تم تسجيل الدخول بنجاح!")
        except Exception as e:
            print(f"❌ خطأ في تسجيل الدخول: {str(e)}")
            traceback.print_exc()

    # ✅ تنفيذ تسجيل الدخول عند بدء السيرفر
    selenium_thread = threading.Thread(target=do_login)
    selenium_thread.start()

@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
