import streamlit as st
import os
import time
import uuid
import bcrypt # مكتبة التشفير
from datetime import datetime
from google import genai
import psycopg2 # استخدام اتصال PostgreSQL المباشر بدلاً من st.connection
from psycopg2 import sql, extras # لاستخدام أوامر SQL الآمنة وتنسيق البيانات

# ===============================================
# 1. إعدادات الصفحة والثوابت
# ===============================================

# اسم جدول المستخدمين (نظراً لأننا نستخدم اتصال مباشر، يمكننا تسميته كما نريد)
USER_TABLE = "zaoujal_users" 
DEFAULT_MAX_QUESTIONS_DAILY = 5
ADMIN_EMAIL = "ahmadzaoujal2009@gmail.com"

st.set_page_config(
    page_title="منصة أحمد للرياضيات (تجربة Gemini)",
    page_icon="🧠",
    layout="wide",
)

# تهيئة حالة الجلسة
if 'gemini_status' not in st.session_state: st.session_state.gemini_status = "❌ لم يتم التهيئة بعد"
if 'db_status' not in st.session_state: st.session_state.db_status = "❌ لم يتم التهيئة بعد"
if 'gemini_client' not in st.session_state: st.session_state.gemini_client = None
if 'db_conn' not in st.session_state: st.session_state.db_conn = None
if 'is_authenticated' not in st.session_state: st.session_state.is_authenticated = False
if 'user_data' not in st.session_state: st.session_state.user_data = None
if 'user_email' not in st.session_state: st.session_state.user_email = None

def display_status():
    """عرض حالة تهيئة النظام في الشريط الجانبي."""
    with st.sidebar.expander("حالة النظام", expanded=True):
        st.caption(f"**حالة Postgres (Supabase):** {st.session_state.db_status}")
        st.caption(f"**حالة عميل Gemini:** {st.session_state.gemini_status}")
        st.caption(f"**حالة المستخدم:** {'✅ تم تسجيل الدخول' if st.session_state.is_authenticated else '❌ تسجيل خروج'}")

# ===============================================
# 2. تهيئة قاعدة البيانات (Postgres / Supabase)
# ===============================================

@st.cache_resource(ttl=3600)
def init_db_connection():
    """يهيئ اتصال قاعدة بيانات Postgres/Supabase باستخدام psycopg2."""
    try:
        # قراءة الأسرار مباشرة من st.secrets
        secrets_data = st.secrets["supabase_db"]
        
        conn = psycopg2.connect(
            host=secrets_data["HOST"],
            dbname=secrets_data["DATABASE"],
            user=secrets_data["USER"],
            password=secrets_data["PASSWORD"],
            port=secrets_data["PORT"]
        )
        st.session_state.db_status = "✅ تم الاتصال بقاعدة البيانات بنجاح"
        return conn

    except KeyError as e:
        st.session_state.db_status = f"❌ خطأ: لم يتم العثور على المفتاح/القسم في Secrets: {e}"
        return None
    except Exception as e:
        # عرض الخطأ الفعلي (مثل فشل المصادقة)
        st.session_state.db_status = f"❌ خطأ حرج في الاتصال بـ Postgres: {e}"
        return None

def setup_database(conn):
    """إنشاء جدول المستخدمين (zaoujal_users) إذا لم يكن موجوداً."""
    if conn is None: return
    try:
        with conn.cursor() as cur:
            # استخدام sql.SQL لبناء أمر DDL آمن
            cur.execute(sql.SQL("""
                CREATE TABLE IF NOT EXISTS {} (
                    email TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    school_grade TEXT,
                    questions_used INTEGER DEFAULT 0,
                    last_use_date DATE,
                    tier TEXT DEFAULT 'Free',
                    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                );
            """).format(sql.Identifier(USER_TABLE)))
        conn.commit()
    except Exception as e:
        st.session_state.db_status = f"❌ خطأ في إعداد جدول Postgres: {e}"

def get_user_data(email):
    """استرداد بيانات المستخدم حسب البريد الإلكتروني."""
    conn = st.session_state.db_conn
    if conn is None: return None
    try:
        with conn.cursor(cursor_factory=extras.DictCursor) as cur:
            cur.execute(sql.SQL("SELECT * FROM {} WHERE email = %s").format(sql.Identifier(USER_TABLE)), (email,))
            return dict(cur.fetchone()) if cur.rowcount > 0 else None
    except Exception as e:
        st.error(f"خطأ في استرداد بيانات المستخدم: {e}")
        return None

def add_user(email, password, grade):
    """إضافة مستخدم جديد."""
    conn = st.session_state.db_conn
    if conn is None or get_user_data(email) is not None: return False

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    today = datetime.now().date() 

    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("""
                INSERT INTO {} (email, user_id, password_hash, school_grade, last_use_date, tier) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """).format(sql.Identifier(USER_TABLE)), (email, str(uuid.uuid4()), hashed_password, grade, today, 'Free'))
            conn.commit()
        return True
    except Exception as e:
        st.error(f"خطأ في إضافة المستخدم: {e}")
        return False

# ===============================================
# 3. تهيئة عميل Gemini
# ===============================================

def init_gemini_client():
    """يهيئ عميل Gemini API."""
    try:
        gemini_api_key = st.secrets["GEMINI_API_KEY"]
        
        if gemini_api_key:
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            st.session_state.gemini_client = genai.Client(api_key=gemini_api_key)
            st.session_state.gemini_status = "✅ تم تهيئة نموذج Gemini بنجاح"
        else:
            st.session_state.gemini_status = "❌ مفتاح Gemini API غير موجود."
            st.session_state.gemini_client = None

    except KeyError:
        st.session_state.gemini_status = "❌ مفتاح Gemini API غير موجود في st.secrets."
        st.session_state.gemini_client = None
    except Exception as e:
        st.session_state.gemini_status = f"❌ خطأ في تهيئة Gemini: {e}"
        st.session_state.gemini_client = None

# ===============================================
# 4. وظائف المصادقة والاستخدام
# ===============================================

def authenticate(email, password):
    """التحقق من بيانات اعتماد المستخدم."""
    user_data = get_user_data(email)
    if user_data:
        # استخدام bcrypt للمقارنة الآمنة (يجب أن تكون كلمة المرور المخزنة مشفرة)
        try:
            if bcrypt.checkpw(password.encode('utf-8'), user_data['password_hash'].encode('utf-8')):
                st.session_state.is_authenticated = True
                st.session_state.user_email = email
                st.session_state.user_data = user_data
                return True
        except ValueError:
            # يحدث إذا كانت كلمة المرور المخزنة غير مشفرة بشكل صحيح (لتجنب هذا، يجب التسجيل بكلمة مرور مشفرة)
            pass 
        
    return False

def login_form():
    """عرض نموذج تسجيل الدخول."""
    st.title("منصة أحمد التعليمية - تسجيل الدخول")
    st.warning("لتسجيل الدخول، استخدم بيانات مستخدم قمت بإنشائه بالفعل.")
    
    with st.form("login_form"):
        st.subheader("تسجيل الدخول")
        email = st.text_input("البريد الإلكتروني")
        password = st.text_input("كلمة المرور", type="password")
        submitted = st.form_submit_button("تسجيل الدخول")

        if submitted:
            if authenticate(email, password):
                st.success("تم تسجيل الدخول بنجاح!")
                time.sleep(1)
                st.rerun()
            else:
                st.error("البريد الإلكتروني أو كلمة المرور غير صحيحة.")
    
    st.markdown("---")
    
    # نموذج تسجيل مستخدم جديد
    with st.form("signup_form"):
        st.subheader("مستخدم جديد - تسجيل")
        new_email = st.text_input("بريد إلكتروني للتسجيل")
        new_password = st.text_input("كلمة مرور (6 أحرف على الأقل)", type="password", min_chars=6)
        grade = st.selectbox("المرحلة الدراسية", ["أولى ثانوي", "ثانية ثانوي", "ثالثة ثانوي (علوم رياضية)", "أخرى"])
        submitted = st.form_submit_button("تسجيل")
        
        if submitted:
            if not new_email or not new_password:
                st.error("الرجاء إدخال البريد الإلكتروني وكلمة المرور.")
            elif add_user(new_email, new_password, grade):
                st.success("تم التسجيل بنجاح! يمكنك الآن تسجيل الدخول.")
                time.sleep(1)
                st.rerun()
            else:
                st.error("فشل التسجيل. ربما البريد الإلكتروني مستخدم بالفعل أو يوجد خطأ في الاتصال.")

def update_usage():
    """تحديث استخدام المستخدم اليومي (لم يتم تنفيذه بالكامل بعد)."""
    # ... (منطق تحديث الاستخدام يمكن إضافته هنا لاحقاً)
    pass
    
# ===============================================
# 5. واجهة المستخدم الرئيسية (بعد المصادقة)
# ===============================================

def main_app_interface():
    """عرض واجهة التطبيق الرئيسية والدردشة مع Gemini."""
    
    st.title("🧠 منصة أحمد التعليمية للرياضيات")
    st.subheader(f"مرحباً بك يا أحمد!")
    st.markdown("""
    **الذكاء الاصطناعي في خدمتك! اطرح أي سؤال رياضي، اطلب حل تمرين معقد، أو اطلب شرح مفهوم غامض.**
    """)
    
    # -----------------
    # واجهة الدردشة (Gemini Chat)
    # -----------------
    
    if st.session_state.gemini_client is None:
        st.warning("لا يمكن الوصول إلى الدردشة. مفتاح Gemini API غير مهيأ.")
        return
        
    # تهيئة سجل الدردشة
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # عرض الرسائل السابقة
    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(message["content"])

    # معالجة مدخلات المستخدم
    if prompt := st.chat_input("اطرح سؤالاً رياضياً أو اطلب شرحاً..."):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # استدعاء Gemini
        try:
            with st.chat_message("assistant"):
                system_instruction = (
                    "أنت معلم رياضيات ذكي وشغوف، متخصص في مناهج المرحلة الثانوية (العلوم الرياضية). "
                    "مهمتك هي شرح المفاهيم، حل التمارين خطوة بخطوة، والرد على أسئلة الطلاب "
                    "بلغة عربية فصحى ومبسطة. يجب عليك دائماً استخدام صيغة LaTeX للمفاهيم الرياضية المعقدة (باستخدام $ $ و $$ $$). "
                    "تذكر أنك تساعد طالب متميز في المرحلة الثانوية."
                )
                
                response = st.session_state.gemini_client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt,
                    config={
                        "system_instruction": system_instruction
                    }
                )
                
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                
        except Exception as e:
            st.error(f"حدث خطأ أثناء التواصل مع نموذج الذكاء الاصطناعي: {e}")
            st.session_state.messages.append({"role": "assistant", "content": "عذراً، حدث خطأ تقني أثناء معالجة طلبك."})


# ===============================================
# 6. نقطة دخول التطبيق
# ===============================================

# 1. تهيئة الخدمات
init_gemini_client()
st.session_state.db_conn = init_db_connection()

# 2. إعداد الجدول عند الاتصال الناجح
if st.session_state.db_conn and st.session_state.db_status.startswith("✅"):
    setup_database(st.session_state.db_conn)

# 3. عرض حالة النظام
display_status()

# 4. عرض الواجهة المناسبة
if st.session_state.is_authenticated:
    main_app_interface()
else:
    # عرض تعليمات الإعداد في حال فشل الاتصال
    if st.session_state.db_status.startswith("❌") or st.session_state.gemini_status.startswith("❌"):
        st.error("⚠️ فشل في تهيئة النظام. يرجى التحقق من الأسرار في Streamlit Cloud.")
        st.markdown("""
        #### إرشادات الإعداد الهامة:
        1. **اذهب إلى Streamlit Cloud** وادخل على "Manage app" ثم "Secrets".
        2. **الصق هذا الكود** في مربع الأسرار (مع تعديل كلمة المرور وتأكيدها):
            ```toml
            GEMINI_API_KEY = "AIzaSyBWEq-XpDoDCFQFRG0sJTzGp7KS06i8I8g"
            
            [supabase_db]
            HOST = "db.pbbeveoeiafbyumlmijd.supabase.co"
            PORT = 5432
            DATABASE = "postgres"
            USER = "postgres"
            PASSWORD = "كلمة المرور الجديدة والصحيحة لقاعدة البيانات" 
            ```
        """)
        
    login_form()

