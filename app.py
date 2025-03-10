import os
import time
import threading
import traceback
from flask import Flask, render_template
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import WebDriverException

app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ ضبط المسارات الصحيحة لمتصفح Chrome على `Railway`
CHROMIUM_PATH = "/usr/bin/chromium"
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"

# ✅ تهيئة WebDriver بشكل صحيح
def init_driver():
    global driver
    try:
        if 'driver' in globals() and driver is not None:
            driver.quit()
            driver = None

        # 🚀 ضبط إعدادات المتصفح
        options = Options()
        options.add_argument("--headless=new")  # ✅ تشغيل بدون واجهة رسومية
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,1024")
        options.binary_location = CHROMIUM_PATH  # ✅ تعيين المسار الصحيح

        # 🚀 تشغيل ChromeDriver
        service = Service(CHROMEDRIVER_PATH)
        driver = webdriver.Chrome(service=service, options=options)
        print("✅ تم تشغيل ChromeDriver بنجاح!")

    except Exception as e:
        print(f"❌ خطأ في تشغيل ChromeDriver: {str(e)}")
        driver = None
        traceback.print_exc()

# ✅ تشغيل المتصفح عند بدء السيرفر
init_driver()

# ✅ تسجيل الدخول تلقائيًا عند بدء التشغيل
def do_login():
   import threading

def keep_alive():
    """محاولة إبقاء المتصفح نشطًا عن طريق إعادة تحميل الصفحة بشكل دوري."""
    global driver
    while True:
        try:
            if driver:
                driver.get("https://office.businmay.net/")
                print("🔄 تم تحديث الجلسة لمنع الإغلاق التلقائي.")
            time.sleep(300)  # تحديث الجلسة كل 5 دقائق
        except Exception as e:
            print(f"⚠️ خطأ في `keep_alive()`: {str(e)}")

# تشغيل `keep_alive` في Thread مستقل
keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
keep_alive_thread.start()

    global driver
    try:
        if driver is None:
            print("⚠️ `driver` غير موجود، إعادة تشغيل `init_driver()`...")
            init_driver()
            if driver is None:
                print("❌ فشل تشغيل `ChromeDriver`، لن يتم تنفيذ `do_login()`")
                return  

        driver.get("https://office.businmay.net/")
        WebDriverWait(driver, 15).until(EC.presence_of_element_located((By.ID, "office_code")))

        driver.execute_script("document.getElementById('office_code').value = '7';")
        driver.execute_script("onCodeChanged('office_id', '7');")
        driver.execute_script("document.getElementById('email').value = 'mahmod.h';")
        driver.execute_script("document.getElementById('password').value = '123';")

        login_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[text()='تسجيل دخول']"))
        )
        time.sleep(1)
        login_btn.click()

        time.sleep(3)
        print("✅ تم تسجيل الدخول بنجاح!")

    except WebDriverException as e:
        print(f"❌ خطأ في `WebDriver`: {str(e)}")
        traceback.print_exc()

    except Exception as e:
        print(f"❌ خطأ في تسجيل الدخول: {str(e)}")
        traceback.print_exc()

# ✅ تشغيل تسجيل الدخول في `Thread` منفصل
selenium_thread = threading.Thread(target=do_login)
selenium_thread.start()

# ✅ تشغيل الموقع
@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))  # ✅ تأكد من أن `PORT` مضبوط بشكل صحيح
    app.run(host="0.0.0.0", port=port)
