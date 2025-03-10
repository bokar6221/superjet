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
from selenium.common.exceptions import InvalidSessionIdException, WebDriverException

app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ متغير للتحكم في `Selenium`
USE_SELENIUM = os.getenv("USE_SELENIUM", "true").lower() == "true"

driver = None  # ✅ تعريف `driver` لتجنب أخطاء `NoneType`

# ✅ تهيئة المتصفح عند الحاجة فقط
def init_driver():
    global driver
    if not USE_SELENIUM:
        print("⚠️ Selenium معطل عبر البيئة، لن يتم تشغيل `init_driver()`")
        return
    
    try:
        if driver:
            driver.quit()

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,1024")
        options.binary_location = "/usr/bin/chromium"

        service = Service("/usr/bin/chromedriver")
        driver = webdriver.Chrome(service=service, options=options)
        print("✅ تم تشغيل ChromeDriver بنجاح!")

    except Exception as e:
        print(f"❌ خطأ في تشغيل ChromeDriver: {str(e)}")
        driver = None
        traceback.print_exc()

# ✅ تشغيل `Selenium` فقط إذا كان مفعّلًا
if USE_SELENIUM:
    init_driver()

# ✅ تسجيل الدخول
def do_login():
    global driver
    if driver is None:
        print("⚠️ `driver` غير مهيأ، إعادة تشغيل `init_driver()`...")
        init_driver()
        if driver is None:
            print("❌ فشل تشغيل `ChromeDriver`")
            return  

    try:
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

    except (InvalidSessionIdException, WebDriverException) as e:
        print(f"⚠️ إعادة تشغيل `Selenium` بسبب الخطأ: {e}")
        init_driver()
        do_login()

    except Exception as e:
        print(f"❌ خطأ في تسجيل الدخول: {str(e)}")
        traceback.print_exc()

# ✅ تشغيل تسجيل الدخول فقط عند الحاجة
if USE_SELENIUM:
    threading.Thread(target=do_login).start()

# ✅ تشغيل الموقع
@app.route('/')
def home():
    return render_template('index.html')

if __name__ == '__main__':
    import time  # تأكد أن هذا السطر داخل `if` وليس خارجه
    while True:
        print("✅ التطبيق يعمل...")
        time.sleep(10)  # إبقاء التطبيق نشطًا حتى لا يُقتل من قبل Railway

    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
