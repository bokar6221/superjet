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

driver = None  # ✅ تعريف المتغير `driver` في النطاق العام
def init_driver():
    global driver  # ✅ تعريف `driver` كمتحول عالمي داخل `init_driver()`
    
    options = Options()
    options.add_argument("--headless=new")
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
    
    driver = webdriver.Chrome(service=service, options=options)  # ✅ حفظ `driver` كمتحول عالمي


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
