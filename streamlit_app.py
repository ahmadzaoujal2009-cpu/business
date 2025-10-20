import streamlit as st
import os
import time
from dotenv import load_dotenv
from google import genai
import psycopg2
from psycopg2 import sql

# قم بتحميل المتغيرات البيئية من ملف .env إذا كان متوفراً (للتشغيل المحلي)
load_dotenv()

# ===============================================
# 1. إعدادات الصفحة وتهيئة حالة الجلسة
# ===============================================

st.set_page_config(
    page_title="أحمد للرياضيات (تجربة Gemini)",
    page_icon="🧠",
    layout="wide",
)

if 'gemini_status' not in st.session_state:
    st.session_state.gemini_status = "❌ لم يتم التهيئة بعد"
if 'db_status' not in st.session_state:
    st.session_state.db_status = "❌ لم يتم التهيئة بعد"
if 'gemini_client' not in st.session_state:
    st.session_state.gemini_client = None
if 'db_conn' not in st.session_state:
    st.session_state.db_conn = None
if 'is_authenticated' not in st.session_state:
    st.session_state.is_authenticated = False
if 'user_role' not in st.session_state:
    st.session_state.user_role = None


def display_status():
    """عرض حالة تهيئة النظام في الشريط الجانبي."""
    with st.sidebar.expander("حالة النظام", expanded=True):
        st.write(f"**حالة Postgres (Supabase):** {st.session_state.db_status}")
        st.write(f"**حالة عميل Gemini:** {st.session_state.gemini_status}")
        st.write(f"**حالة المستخدم:** {'✅ تم تسجيل الدخول' if st.session_state.is_authenticated else '❌ تسجيل خروج'}")

# ===============================================
# 2. تهيئة قاعدة البيانات (Postgres / Supabase)
# ===============================================

@st.cache_resource(ttl=3600)
def init_db_connection():
    """يهيئ اتصال قاعدة بيانات Postgres/Supabase."""
    try:
        # القراءة من st.secrets مباشرة (الشكل الجديد)
        db_config = st.secrets["supabase_db"]
        
        # بناء سلسلة الاتصال
        conn_string = (
            f"host={db_config['HOST']} "
            f"dbname={db_config['DATABASE']} "
            f"user={db_config['USER']} "
            f"password={db_config['PASSWORD']} "
            f"port={db_config['PORT']}"
        )
        
        conn = psycopg2.connect(conn_string)
        st.session_state.db_status = "✅ تم الاتصال بقاعدة البيانات بنجاح"
        return conn

    except KeyError:
        # يُعرض هذا الخطأ إذا لم يتم العثور على القسم [supabase_db]
        st.session_state.db_status = "❌ خطأ: لم يتم العثور على قسم [supabase_db] في secrets.toml."
        return None
    except Exception as e:
        st.session_state.db_status = f"❌ خطأ في إعداد جدول Postgres: {e}"
        return None

# ===============================================
# 3. تهيئة عميل Gemini
# ===============================================

def init_gemini_client():
    """يهيئ عميل Gemini API."""
    try:
        # 1. محاولة القراءة من المفتاح الفردي (الطريقة المفضلة في Streamlit Cloud)
        gemini_api_key = st.secrets.get("GEMINI_API_KEY") 

        # 2. إذا لم يتم العثور عليه، محاولة القراءة من القسم القديم (للتوافق)
        if not gemini_api_key:
            gemini_api_key = st.secrets.get("ai_config", {}).get("GEMINI_API_KEY")
        
        if gemini_api_key:
            # تعيين المفتاح كمتغير بيئة للاستخدام بواسطة مكتبة Google GenAI
            os.environ["GEMINI_API_KEY"] = gemini_api_key
            st.session_state.gemini_client = genai.Client(api_key=gemini_api_key)
            st.session_state.gemini_status = "✅ تم تهيئة نموذج Gemini بنجاح"
        else:
            st.session_state.gemini_status = "❌ مفتاح Gemini API غير موجود."
            st.session_state.gemini_client = None

    except Exception as e:
        st.session_state.gemini_status = f"❌ خطأ في تهيئة Gemini: {e}"
        st.session_state.gemini_client = None


# ===============================================
# 4. وظائف مصادقة المستخدم
# ===============================================

def authenticate(username, password):
    """التحقق من بيانات اعتماد المستخدم مقابل قاعدة البيانات."""
    conn = st.session_state.db_conn
    if conn is None or conn.closed == 1:
        st.error("خطأ في الاتصال بقاعدة البيانات. لا يمكن تسجيل الدخول.")
        return False

    try:
        with conn.cursor() as cur:
            # استخدام SQL معلمات لمنع هجمات حقن SQL
            cur.execute(
                sql.SQL("SELECT role, password_hash FROM users WHERE username = %s"),
                (username,)
            )
            result = cur.fetchone()
            
            if result:
                role, stored_hash = result
                # في تطبيق حقيقي، يجب مقارنة الهاش
                # بما أننا لا نملك مكتبة تشفير هنا، نكتفي بمقارنة بسيطة للمثال
                if password == stored_hash: # في المثال، نعتبر الهاش هو كلمة المرور نفسها
                    st.session_state.is_authenticated = True
                    st.session_state.user_role = role
                    return True
            return False

    except Exception as e:
        st.error(f"خطأ أثناء التحقق من قاعدة البيانات: {e}")
        return False

def login_form():
    """عرض نموذج تسجيل الدخول."""
    with st.form("login_form"):
        st.subheader("تسجيل الدخول")
        username = st.text_input("اسم المستخدم")
        password = st.text_input("كلمة المرور (للتجربة: use 'ahmad' and '2009' )", type="password")
        submitted = st.form_submit_button("تسجيل الدخول")

        if submitted:
            if authenticate(username, password):
                st.success("تم تسجيل الدخول بنجاح!")
                st.rerun()
            else:
                st.error("اسم المستخدم أو كلمة المرور غير صحيحة.")

# ===============================================
# 5. واجهة المستخدم الرئيسية (بعد المصادقة)
# ===============================================

def main_app_interface():
    """عرض واجهة التطبيق الرئيسية والدردشة مع Gemini."""
    
    st.title("🧠 منصة أحمد التعليمية للرياضيات")
    st.subheader(f"مرحباً بك، {st.session_state.user_role}!")
    st.markdown("""
    **ابدأ في استكشاف دروس الرياضيات، حل التمارين، أو اطرح سؤالاً مباشراً على مساعد Gemini الذكي.**
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
        # إضافة رسالة المستخدم إلى السجل وعرضها
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # استدعاء Gemini
        try:
            with st.chat_message("assistant"):
                # نظام التعليمات لمساعدة Gemini في التخصص
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
                
                # عرض استجابة النموذج وإضافتها إلى السجل
                st.markdown(response.text)
                st.session_state.messages.append({"role": "assistant", "content": response.text})
                
        except Exception as e:
            st.error(f"حدث خطأ أثناء التواصل مع نموذج الذكاء الاصطناعي: {e}")
            st.session_state.messages.append({"role": "assistant", "content": "عذراً، حدث خطأ تقني أثناء معالجة طلبك."})


# ===============================================
# 6. نقطة دخول التطبيق
# ===============================================

# 1. تهيئة الخدمات قبل تشغيل أي شيء
init_gemini_client()
st.session_state.db_conn = init_db_connection()

# 2. عرض حالة النظام
display_status()

# 3. عرض الواجهة المناسبة
if st.session_state.is_authenticated:
    main_app_interface()
else:
    # عرض تعليمات الإعداد في حال فشل الاتصال بالخدمات
    if st.session_state.db_status.startswith("❌") or st.session_state.gemini_status.startswith("❌"):
        st.error("⚠️ فشل في تهيئة النظام. يرجى التحقق من المفاتيح السرية (Secrets) في Streamlit Cloud.")
        st.markdown("""
        #### إرشادات الإعداد الهامة:
        1.  **اذهب إلى Streamlit Cloud** وادخل على "Manage app" ثم "Secrets".
        2.  **انسخ هذا الكود** والصقه في مربع الأسرار (مع تعديل كلمة المرور):
            ```toml
            GEMINI_API_KEY = "AIzaSyBWEq-XpDoDCFQFRG0sJTzGp7KS06i8I8g"
            
            [supabase_db]
            HOST = "db.pbbeveoeiafbyumlmijd.supabase.co"
            PORT = 5432
            DATABASE = "postgres"
            USER = "postgres"
            PASSWORD = "كلمة المرور الجديدة التي اخترتها هنا" # <-- تأكد من مطابقتها لـ Supabase
            ```
        3.  **احفظ المفاتيح** وانتظر إعادة تشغيل التطبيق.
        """)
        
    login_form()

