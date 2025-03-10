import os
import time
import traceback
from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ ضبط متغيرات البيئة لـ `Chromium` و `ChromeDriver`
CHROMIUM_PATH = "/usr/bin/chromium"
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"

# ✅ تهيئة `Selenium` لاستخدام `Chromium` في `Docker`
def init_driver():
    options = Options()

    # ✅ تشغيل `Chromium` بدون واجهة رسومية وتحسين الاستقرار
    options.add_argument("--headless=new")  # استخدام `headless` الجديد
    options.add_argument("--no-sandbox")  # تعطيل `sandbox` لتجنب الأعطال
    options.add_argument("--disable-dev-shm-usage")  # منع استخدام `/dev/shm`
    options.add_argument("--disable-gpu")  # تعطيل الـ GPU لأنه غير مدعوم في `Railway`
    options.add_argument("--disable-software-rasterizer")  # منع مشاكل `GPU rasterizer`
    options.add_argument("--disable-blink-features=AutomationControlled")  # إخفاء Selenium عن مواقع الويب
    options.add_argument("--window-size=1920,1080")  # زيادة حجم النافذة لتجنب مشاكل التصميم

    # ✅ تحديد موقع `Chromium`
    options.binary_location = "/usr/bin/chromium"

    # ✅ تشغيل `ChromeDriver`
    service = Service("/usr/bin/chromedriver")

    return webdriver.Chrome(service=service, options=options)

driver = init_driver()


# ✅ تشغيل `Selenium` عند بدء السيرفر
driver = init_driver()

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

def do_login():
    """تنفيذ تسجيل الدخول تلقائيًا بعد التأكد من تحميل الصفحة."""
    try:
        driver.get("https://office.businmay.net/")

        # ✅ الانتظار حتى يظهر العنصر قبل محاولة التفاعل معه
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.ID, "office_code")))

        driver.execute_script("document.getElementById('office_code').value = '7';")
        driver.execute_script("onCodeChanged('office_id', '7');")
        driver.execute_script("document.getElementById('email').value = 'mahmod.h';")
        driver.execute_script("document.getElementById('password').value = '123';")

        login_btn = WebDriverWait(driver, 10).until(
            EC.element_to_be_clickable((By.XPATH, "//button[text()='تسجيل دخول']"))
        )
        login_btn.click()

        time.sleep(3)  # ✅ انتظار تحميل الصفحة بعد تسجيل الدخول
        print("✅ تم تسجيل الدخول بنجاح!")
    except Exception as e:
        print(f"❌ خطأ في تسجيل الدخول: {str(e)}")
        traceback.print_exc()

# تنفيذ تسجيل الدخول عند بدء السيرفر
do_login()

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/booking')
def booking_page():
    from_code = request.args.get('from_code')
    to_code = request.args.get('to_code')
    date_str = request.args.get('date')

    return render_template(
        'booking.html',
        from_office=from_code,
        to_office=to_code,
        date_str=date_str
    )

@app.route('/api/booking', methods=['GET'])
def get_booking_data():
    """جلب بيانات الرحلات بناءً على المدينة والتاريخ."""
    from_code = request.args.get('from_code')
    to_code = request.args.get('to_code')
    date_str = request.args.get('date')

    try:
        driver.get("https://office.businmay.net/ar/new-reservationBookingRequests")
        time.sleep(2)

        script_set_val = """
            var elems = document.getElementsByName(arguments[0]);
            for (var i = 0; i < elems.length; i++) {
                elems[i].value = arguments[1];
                var event = document.createEvent('HTMLEvents');
                event.initEvent('change', true, false);
                elems[i].dispatchEvent(event);
            }
        """
        driver.execute_script(script_set_val, "from_date", date_str)
        driver.execute_script(script_set_val, "to_date", date_str)
        driver.execute_script(script_set_val, "from_office_id", from_code)
        driver.execute_script(script_set_val, "to_office_id", to_code)

        search_btn = driver.find_element("xpath", "//button[@type='submit']")
        search_btn.click()
        time.sleep(3)

        rows = driver.find_elements("css selector", "table.table-bordered tbody tr")
        times_data = []

        for row in rows:
            tds = row.find_elements("tag name", "td")
            if len(tds) >= 7:
                trip_time = tds[6].text.strip()
                if trip_time:
                    price = tds[8].text.strip() if len(tds) >= 9 else ""
                    remaining = tds[7].text.strip() if len(tds) >= 8 else ""
                    times_data.append({
                        "time": trip_time,
                        "price": price,
                        "remaining": remaining
                    })

        return jsonify({"times": times_data})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "details": traceback.format_exc()}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
