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

# ✅ تهيئة المتصفح
def init_driver():
    global driver
    try:
        if 'driver' in globals() and driver is not None:
            driver.quit()
            driver = None

        options = Options()
        options.add_argument("--headless")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--window-size=1280,1024")
        options.add_argument("--remote-debugging-port=9222")
        options.binary_location = "/usr/bin/chromium"

        service = Service("/usr/bin/chromedriver")
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
    global driver
    try:
        if driver is None:
            print("⚠️ `driver` لم يتم تهيئته بعد، إعادة تشغيل `init_driver()`...")
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

        for _ in range(5):  
            try:
                login_btn = WebDriverWait(driver, 10).until(
                    EC.element_to_be_clickable((By.XPATH, "//button[text()='تسجيل دخول']"))
                )
                time.sleep(1)
                login_btn.click()
                break  
            except Exception as e:
                print(f"⚠️ إعادة محاولة النقر على الزر: {e}")
                time.sleep(2)

        time.sleep(3)
        print("✅ تم تسجيل الدخول بنجاح!")

    except InvalidSessionIdException:
        print("⚠️ الجلسة غير صالحة، إعادة تشغيل `ChromeDriver`...")
        init_driver()
        do_login()

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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
