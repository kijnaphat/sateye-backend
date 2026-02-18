import os
import re
import requests
from fastapi import FastAPI, Request
from supabase import create_client

app = FastAPI()

# --- Config (ดึงจาก Render Environment) ---
SUPABASE_URL = os.environ.get("https://oofvlljgfisznvexigxz.supabase.co")
SUPABASE_KEY = os.environ.get("sb_publishable_hwPTId3EwzDlwBaI-2P9wQ_A0KpRGDN")
TELEGRAM_TOKEN = os.environ.get("8063302361:AAFkZgX8o740whuNFfZyatd_fMQXmpzGqrY")
# TYPHOON_API_KEY = os.environ.get("TYPHOON_API_KEY") # (อนาคต)

# เชื่อมต่อ Database
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# --- ฟังก์ชันช่วยดึง WBS (I, P, C) ---
def extract_pea_wbs(text):
    """
    ค้นหา WBS ที่ขึ้นต้นด้วย I, P, C จากข้อความ
    ตัวอย่างที่จับได้: C-041-66.01, P.12345, I-67-001
    """
    # Pattern: เริ่มคำ + [IPC] + (ขีด/จุด/ไม่มีก็ได้) + ตัวเลขยาวๆ
    pattern = r"\b[IPC][-.]?[\d.-]+\b"
    match = re.search(pattern, text, re.IGNORECASE)
    
    if match:
        return match.group(0).upper() # คืนค่าตัวพิมพ์ใหญ่
    return None

# --- ฟังก์ชันส่งข้อความเข้า Telegram ---
def send_msg(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": chat_id, "text": text
    })

# --- ฟังก์ชันประมวลผล OCR (จำลอง) ---
# *อนาคต: ตรงนี้ต้องรับ text จริงๆ จาก Typhoon OCR มาใส่แทน raw_text_mock*
def process_ocr_mock(image_url):
    print(f"Processing Image: {image_url}")
    
    # [จำลอง] สมมติว่า OCR อ่านข้อความดิบๆ มาได้แบบนี้ (มี WBS ปนอยู่)
    raw_text_mock = """
    การไฟฟ้าส่วนภูมิภาค
    งานขยายเขตระบบจำหน่ายไฟฟ้า หมู่ 5
    รหัสงาน C-041-67.001.5
    งบประมาณ 250,000 บาท
    ระยะทางแรงสูง 120.5 เมตร
    """
    
    # 1. ใช้ฟังก์ชันเทพ ดึง WBS ออกมา
    detected_wbs = extract_pea_wbs(raw_text_mock)
    final_wbs = detected_wbs if detected_wbs else "UNKNOWN"

    # 2. จัดข้อมูลใส่ JSON ให้ตรงกับตาราง SQL เป๊ะๆ
    return {
        "wbs_code": final_wbs,                    # ได้ค่า C-041-67.001.5
        "job_name": "ขยายเขตระบบจำหน่าย (ทดสอบ)",
        "contact_number": "081-999-8888",
        
        "approver_name": "ผจก. สมชาย",
        "approval_date": "2024-02-20",
        "budget": 250000.00,
        
        "assignment_date": "2024-02-21",
        "supervisor_name": "นายช่างใหญ่",
        "contractor_name": "บริษัท ไฟฟ้าไทย จำกัด",
        "signature_text": "มีลายเซ็นครบ",
        
        # --- ข้อมูลเทคนิค (Key ตรงกับ DB ใหม่) ---
        "hv_distance_meter": 120.5,
        "lv_distance_meter": 400.0,
        "transformer_size_kva": 160,
        "pole_size": "12.00 ม.",
        "pole_quantity": 10,
        
        "status": "Pending",
        "location_coordinates": "13.7563, 100.5018",
        
        # --- วันที่อื่นๆ (ใส่ None ไปก่อนถ้าไม่มี) ---
        "energize_date": None,
        "closing_date": None,
        "post_gis_date": None
    }

@app.get("/")
def home():
    return {"status": "Bot Online 🟢"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    data = await request.json()
    
    # ตรวจสอบว่ามีรูปภาพส่งมาไหม
    if 'message' in data and 'photo' in data['message']:
        chat_id = data['message']['chat']['id']
        photo = data['message']['photo'][-1] # เอาชิ้นใหญ่สุด
        file_id = photo['file_id']
        
        send_msg(chat_id, "⏳ กำลังอ่านเอกสาร...")
        
        try:
            # 1. ขอ URL รูปจาก Telegram
            r = requests.get(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getFile?file_id={file_id}")
            if r.status_code != 200:
                raise Exception("ไม่สามารถดึงรูปจาก Telegram ได้")
                
            file_path = r.json()['result']['file_path']
            image_url = f"https://api.telegram.org/file/bot{TELEGRAM_TOKEN}/{file_path}"
            
            # 2. ประมวลผล (Mock OCR)
            extracted_data = process_ocr_mock(image_url)
            
            # 3. ใส่ File ID เพื่อให้ดูรูปย้อนหลังได้
            extracted_data['telegram_file_id'] = file_id 
            
            # 4. บันทึกลง Supabase
            supabase.table('project_jobs').insert(extracted_data).execute()
            
            # 5. แจ้งผลสำเร็จ
            msg = f"✅ บันทึกแล้ว!\nWBS: {extracted_data['wbs_code']}\nงบ: {extracted_data['budget']:,} บ."
            send_msg(chat_id, msg)
            
        except Exception as e:
            print(f"Error: {e}")
            send_msg(chat_id, f"❌ เกิดข้อผิดพลาด: {str(e)}")
            
    return "OK"