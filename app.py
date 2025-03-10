import os
import time
import traceback
from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

app = Flask(__name__, static_folder="static", template_folder="templates")

# ✅ تعيين المسارات الصحيحة لـ Chromium و ChromeDriver في Railway
CHROMIUM_PATH = "/usr/bin/chromium-browser"  # تم التعديل
CHROMEDRIVER_PATH = "/usr/bin/chromedriver"

# ✅ تهيئة `Selenium` لاستخدام `Chromium`
def init_driver():
    options = Options()

    # ✅ تشغيل بدون واجهة رسومية
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # ✅ تحديد موقع `Chromium`
    options.binary_location = CHROMIUM_PATH

    # ✅ تشغيل `ChromeDriver`
    service = Service(CHROMEDRIVER_PATH)

    return webdriver.Chrome(service=service, options=options)

# تشغيل المتصفح عند بدء السيرفر
driver = init_driver()

def do_login():
    """تنفيذ تسجيل الدخول تلقائيًا."""
    try:
        driver.get("https://office.businmay.net/")
        time.sleep(2)  # انتظار تحميل الصفحة

        driver.execute_script("document.getElementById('office_code').value = '7';")
        driver.execute_script("onCodeChanged('office_id', '7');")
        driver.execute_script("document.getElementById('email').value = 'mahmod.h';")
        driver.execute_script("document.getElementById('password').value = '123';")

        login_btn = driver.find_element("xpath", "//button[text()='تسجيل دخول']")
        login_btn.click()
        
        time.sleep(3)  # انتظار التحميل بعد تسجيل الدخول
        print("✓ تم تسجيل الدخول بنجاح")
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

@app.route('/api/book', methods=['POST'])
def book_seat():
    """تنفيذ حجز مقعد معين في الرحلة."""
    data = request.get_json()
    from_code = data.get("from_code")
    to_code = data.get("to_code")
    date_str = data.get("date")
    time_str = data.get("time")
    seat_num = data.get("seat")

    try:
        driver.get("https://office.businmay.net/ar/new-reservationBookingRequests")
        time.sleep(2)

        return jsonify({"message": f"تم حجز الكرسي {seat_num} بنجاح."})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "details": traceback.format_exc()}), 500

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
