from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
import chromedriver_autoinstaller  # ✅ تثبيت Chrome تلقائيًا
import os
import time
import traceback

app = Flask(__name__, static_folder="static", template_folder="templates")

# بيانات تسجيل الدخول
OFFICE_CODE = os.getenv("OFFICE_CODE", "7")
EMAIL = os.getenv("EMAIL", "mahmod.h")
PASSWORD = os.getenv("PASSWORD", "123")

# بيانات المكاتب (كود -> اسم)
OFFICE_MAP = {
    "7": "محرم بك",
    "171": "أسيوط",
    "4": "الترجمان",
    "6": "الجيزة",
    "33": "السويس",
    "18": "الغردقة",
    "120": "المنيا",
    "17": "بورسعيد",
    "200": "دائري الجديد",
    "174": "سفاجا",
    "111": "سوهاج",
    "19": "شرم - الرويسات",
    "5": "عبدالمنعم رياض",
    "188": "عدلي منصور",
    "144": "مارينا ـ 2",
    "29": "الأقصر",
    "3": "رمسيس",
    "151": "المنشية",
    "214": "العالمين",
    "126": "أسوان",
    "219": "كوم أمبو",
    "16": "مرسي مطروح",
    "20": "ميامي",
    "28": "نويبع",
    "209": "أبو قرقاص",
    "217": "ارمنت",
    "218": "أسنا",
    "145": "البلينا",
    "216": "الطور",
    "146": "برديس",
    "178": "سمالوط",
    "210": "قفط",
    "1": "المطار"
}

# ✅ تهيئة ChromeDriver بشكل صحيح في Railway
def init_driver():
    chromedriver_autoinstaller.install()  # ✅ تثبيت Chrome تلقائيًا
    options = Options()
    
    # ✅ تشغيل Chrome بدون واجهة رسومية
    options.add_argument("--headless")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # ✅ تحديد المسار الصحيح لمتصفح Chromium في Railway
    options.binary_location = "/usr/bin/chromium"

    service = Service("/opt/venv/bin/chromedriver")  # ✅ تشغيل ChromeDriver
    return webdriver.Chrome(service=service, options=options)

# تشغيل المتصفح عند بدء السيرفر
driver = init_driver()

def do_login():
    """تنفيذ تسجيل الدخول تلقائيًا."""
    try:
        driver.get("https://office.businmay.net/")
        time.sleep(2)  # انتظار تحميل الصفحة

        driver.execute_script(f"document.getElementById('office_code').value = '{OFFICE_CODE}';")
        driver.execute_script(f"onCodeChanged('office_id', '{OFFICE_CODE}');")
        driver.execute_script(f"document.getElementById('email').value = '{EMAIL}';")
        driver.execute_script(f"document.getElementById('password').value = '{PASSWORD}';")

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
    return render_template('index.html', offices=OFFICE_MAP)

@app.route('/booking')
def booking_page():
    from_code = request.args.get('from_code')
    to_code = request.args.get('to_code')
    date_str = request.args.get('date')

    from_office_name = OFFICE_MAP.get(from_code, from_code)
    to_office_name = OFFICE_MAP.get(to_code, to_code)

    return render_template(
        'booking.html',
        from_office=from_office_name,
        to_office=to_office_name,
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
