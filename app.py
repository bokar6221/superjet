from flask import Flask, render_template, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import StaleElementReferenceException
from webdriver_manager.chrome import ChromeDriverManager
import time
import traceback

app = Flask(__name__)

# بيانات تسجيل الدخول
OFFICE_CODE = "7"
EMAIL = "mahmod.h"
PASSWORD = "123"

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

# متغيرات عامة
driver_global = None
wait_global = None
logged_in = False

def init_driver_headless():
    """تهيئة متصفح Chrome بوضع headless."""
    global driver_global, wait_global
    # إذا كان هناك متصفح مفتوح مسبقًا، أغلقه
    if driver_global:
        driver_global.quit()

    options = ChromeOptions()
    # وضع headless لتسريع التصفح بدون واجهة
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])

    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options
    )
    # تقليل أزمنة الانتظار لجعل العملية أسرع
    driver.implicitly_wait(5)   # انتظار ضمني
    wait = WebDriverWait(driver, 10)  # انتظار صريح

    driver_global = driver
    wait_global = wait
    return driver

def do_login():
    """تنفيذ تسجيل الدخول."""
    global logged_in
    driver = driver_global
    wait = wait_global

    try:
        driver.get("https://office.businmay.net/")
        wait.until(EC.presence_of_element_located((By.ID, "office_code")))

        # تعبئة حقول تسجيل الدخول
        driver.execute_script(f"document.getElementById('office_code').value = '{OFFICE_CODE}';")
        driver.execute_script(f"onCodeChanged('office_id', '{OFFICE_CODE}');")
        driver.execute_script(f"document.getElementById('email').value = '{EMAIL}';")
        driver.execute_script(f"document.getElementById('password').value = '{PASSWORD}';")

        login_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[text()='تسجيل دخول']")))
        login_btn.click()

        wait.until(EC.url_contains("dashboard"))
        logged_in = True
        print("✓ تم تسجيل الدخول بنجاح")
    except Exception as e:
        print(f"❌ خطأ في تسجيل الدخول: {str(e)}")
        traceback.print_exc()

@app.route('/')
def home():
    return render_template('index.html', offices=OFFICE_MAP)

@app.route('/booking')
def booking_page():
    from_code = request.args.get('from_code')
    to_code = request.args.get('to_code')
    date_str = request.args.get('date')

    # عرض اسم المكتب بدل الكود
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
    driver = driver_global
    wait = wait_global

    from_code = request.args.get('from_code')
    to_code = request.args.get('to_code')
    date_str = request.args.get('date')
    time_str = request.args.get('time')  # إذا وجد، نعرض الكراسي

    try:
        driver.get("https://office.businmay.net/ar/new-reservationBookingRequests")

        # حقول البحث
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

        # محاولة النقر على زر البحث مع إعادة المحاولة إذا حدث StaleElementReferenceException
        for _ in range(2):
            try:
                search_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']")))
                search_btn.click()
                break
            except StaleElementReferenceException:
                driver.refresh()
                time.sleep(2)

        # انتظار ظهور الصفوف
        rows = wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "table.table-bordered tbody tr"))
        times_data = []

        # جمع بيانات المواعيد
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
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

        result = {"times": times_data}

        # إذا تم تمرير time_str نعرض الكراسي
        if time_str:
            chosen_row = None
            for row in rows:
                tds = row.find_elements(By.TAG_NAME, "td")
                if len(tds) >= 7 and tds[6].text.strip() == time_str:
                    chosen_row = row
                    break
            if not chosen_row:
                return jsonify({"error": "الوقت غير موجود"}), 404

            # نقر مزدوج على الصف
            ActionChains(driver).double_click(chosen_row).perform()
            wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "chair")))
            chairs = driver.find_elements(By.CLASS_NAME, "chair")

            seats_data = []
            for chair in chairs:
                seat_text = chair.text.strip()
                style = chair.get_attribute("style").lower().replace(" ", "")
                if seat_text:
                    seat_lines = seat_text.split("\n")
                    try:
                        seat_num = int(seat_lines[0])
                    except:
                        continue
                    status = "available" if ("#ffff54" in style or "rgb(255,255,84)" in style) else "unavailable"
                    seats_data.append({
                        "number": seat_num,
                        "status": status
                    })
            result["seats"] = seats_data

        return jsonify(result)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "details": traceback.format_exc()}), 500

@app.route('/api/book', methods=['POST'])
def book_seat():
    driver = driver_global
    wait = wait_global
    data = request.get_json()

    from_code = data.get("from_code")
    to_code = data.get("to_code")
    date_str = data.get("date")
    time_str = data.get("time")
    seat_num = data.get("seat")

    try:
        driver.get("https://office.businmay.net/ar/new-reservationBookingRequests")

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

        # زر البحث مع إعادة المحاولة
        for _ in range(2):
            try:
                search_btn = wait.until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit']")))
                search_btn.click()
                break
            except StaleElementReferenceException:
                driver.refresh()
                time.sleep(2)

        rows = wait.until(lambda d: d.find_elements(By.CSS_SELECTOR, "table.table-bordered tbody tr"))
        chosen_row = None
        for row in rows:
            tds = row.find_elements(By.TAG_NAME, "td")
            if len(tds) >= 7 and tds[6].text.strip() == time_str:
                chosen_row = row
                break
        if not chosen_row:
            return jsonify({"error": f"لم يتم العثور على الرحلة بالموعد: {time_str}"}), 404

        ActionChains(driver).double_click(chosen_row).perform()
        wait.until(EC.presence_of_all_elements_located((By.CLASS_NAME, "chair")))
        chairs = driver.find_elements(By.CLASS_NAME, "chair")

        target_chair = None
        for chair in chairs:
            seat_text = chair.text.strip()
            style = chair.get_attribute("style").lower().replace(" ", "")
            if seat_text:
                seat_lines = seat_text.split("\n")
                try:
                    s_num = int(seat_lines[0])
                except:
                    continue
                if s_num == int(seat_num) and ("#ffff54" in style or "rgb(255,255,84)" in style):
                    target_chair = chair
                    break

        if not target_chair:
            return jsonify({"error": f"الكـرسي رقم {seat_num} غير متاح للحجز."}), 400

        target_chair.click()
        time.sleep(1)
        confirm_btn = driver.find_element(By.XPATH, "//button[contains(text(),'تأكيد وطباعة')]")
        confirm_btn.click()
        time.sleep(2)

        return jsonify({"message": f"تم حجز الكرسي {seat_num} بنجاح."})

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e), "details": traceback.format_exc()}), 500

if __name__ == '__main__':
    # 1) تهيئة المتصفح
    init_driver_headless()
    # 2) تسجيل الدخول مرة واحدة قبل تشغيل السيرفر
    do_login()
    # 3) تشغيل السيرفر
    app.run(debug=True)
