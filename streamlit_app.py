import streamlit as st
import bcrypt
import os 
import uuid
from datetime import datetime
from PIL import Image
from google import genai
import pandas as pd 
from urllib.parse import quote_plus
import sys
# ✅ الاستيراد الجديد المطلوب لحل مشكلة 'textual SQL expression'
from sqlalchemy import text 

# -------------------- 1. الثوابت والإعداد الأولي --------------------

DEFAULT_MAX_QUESTIONS_DAILY = 5 
ADMIN_EMAIL = "ahmadzaoujal2009@gmail.com" 
USER_TIERS = {
    "Free": 5,        
    "Standard": 20,   
    "Unlimited": 9999 
}
USER_TABLE = "zaoujal_users" 

# -----------------
# 2. Postgres Connection and Initialization
# -----------------

def get_postgres_conn():
    """
    يتصل بقاعدة بيانات Postgres باستخدام البيانات المفككة من secrets.toml وتجميعها في Python.
    """
    try:
        # 🔑 جلب البيانات من القسم [supabase_db] في secrets.toml
        db_config = st.secrets.get("supabase_db")
        
        if not db_config:
            st.session_state.db_status = "❌ خطأ: لم يتم العثور على قسم [supabase_db] في secrets.toml."
            return None
            
        if not all(k in db_config for k in ['HOST', 'PORT', 'DATABASE', 'USER', 'PASSWORD']):
            st.session_state.db_status = "❌ خطأ: مفاتيح الاتصال ناقصة. تأكد من وجود HOST, PORT, إلخ."
            return None

        # ترميز كلمة المرور واليوزر لضمان عمل الرموز الخاصة مثل @
        password_encoded = quote_plus(str(db_config['PASSWORD']))
        user_encoded = quote_plus(str(db_config['USER']))
        
        # تجميع سلسلة الاتصال (URI)
        conn_string = (
            f"postgresql://{user_encoded}:"
            f"{password_encoded}@"
            f"{db_config['HOST']}:"
            f"{db_config['PORT']}/"
            f"{db_config['DATABASE']}"
        )
        
        # استخدام st.connection بـ URI الذي تم بناؤه يدوياً
        conn = st.connection("supabase_db_assembled", type="sql", url=conn_string)
        st.session_state.db_status = "✅ تم الاتصال بـ Supabase (Postgres) بنجاح"
        return conn
    except Exception as e:
        error_type, error_obj, traceback_obj = sys.exc_info()
        st.session_state.db_status = f"❌ خطأ حرج في الاتصال بـ Supabase: {error_type.__name__}: {e}"
        return None

# تهيئة حالة الجلسة
if 'db_status' not in st.session_state:
    st.session_state.db_status = "جاري التحقق من اتصال قاعدة البيانات..."
    
# الاتصال بقاعدة البيانات وإعدادها
postgres_conn = None
try:
    postgres_conn = get_postgres_conn()
    if postgres_conn:
        # دالة مصححة لإنشاء الجدول باستخدام text()
        def setup_database(conn):
            """إنشاء جدول المستخدمين إذا لم يكن موجوداً."""
            try:
                # ✅ استخدام text(...) لتصريح صريح بنص SQL
                with conn.session as session:
                    session.execute(text(f"""
                        CREATE TABLE IF NOT EXISTS {USER_TABLE} (
                            email TEXT PRIMARY KEY,
                            user_id TEXT NOT NULL,
                            password_hash TEXT NOT NULL,
                            school_grade TEXT,
                            questions_used INTEGER DEFAULT 0,
                            last_use_date DATE,
                            tier TEXT DEFAULT 'Free',
                            created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
                        );
                    """))
                    session.commit()
            except Exception as e:
                st.session_state.db_status = f"❌ خطأ في إعداد جدول Postgres: {e}"
        setup_database(postgres_conn)
except Exception as e:
    st.session_state.db_status = f"❌ فشل الإعداد الأولي: {e}"


# -----------------
# 3. Gemini Client Initialization
# -----------------

try:
    gemini_api_key = st.secrets.get("ai_config", {}).get("GEMINI_API_KEY")
    if gemini_api_key:
        os.environ["GEMINI_API_KEY"] = gemini_api_key
        st.session_state.gemini_client = genai.Client(api_key=gemini_api_key)
        st.session_state.gemini_status = "✅ تم تهيئة نموذج Gemini بنجاح"
    else:
        st.session_state.gemini_status = "❌ مفتاح Gemini API غير موجود."
except Exception as e:
    st.session_state.gemini_status = f"❌ خطأ في تهيئة Gemini: {e}"
    st.session_state.gemini_client = None


# -----------------
# 4. دوال Postgres لإدارة المستخدمين
# -----------------

def postgres_add_user(email, password, grade):
    """إضافة مستخدم جديد إلى Postgres (مصحح)."""
    if postgres_conn is None: return False
    
    # التحقق من وجود المستخدم
    if postgres_get_user_data(email) is not None:
        return False 

    hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    today = datetime.now().date() 

    try:
        # ✅ استخدام session.execute
        with postgres_conn.session as session:
            # هنا لا نحتاج text() لأننا نستخدم params
            session.execute(f"""
                INSERT INTO {USER_TABLE} (email, user_id, password_hash, school_grade, last_use_date, tier) 
                VALUES (%s, %s, %s, %s, %s, %s)
            """, params=(email, str(uuid.uuid4()), hashed_password, grade, today, 'Free'))
            session.commit()
        return True
    except Exception as e:
        st.error(f"خطأ في إضافة المستخدم: {e}")
        return False

def postgres_get_user_data(email):
    """جلب بيانات المستخدم من Postgres."""
    if postgres_conn is None: return None
    try:
        # استخدام .query مع ttl=0 لضمان الحصول على أحدث البيانات
        rows_df = postgres_conn.query(f"SELECT * FROM {USER_TABLE} WHERE email = %s", params=[email], ttl=0)
        
        if not rows_df.empty:
            user_data = rows_df.iloc[0].to_dict() 
            return user_data
        return None
    except Exception as e:
        # يتم تجاهل الأخطاء بهدوء هنا
        return None

def postgres_update_usage(email, increment=False):
    """تحديث عدد استخدامات المستخدم وإعادة تعيينها يومياً."""
    if postgres_conn is None: return False, 0, DEFAULT_MAX_QUESTIONS_DAILY
    
    user_data = postgres_get_user_data(email)
    
    if user_data is None: 
        return False, 0, DEFAULT_MAX_QUESTIONS_DAILY
        
    user_tier = user_data.get('tier', 'Free')
    user_limit = USER_TIERS.get(user_tier, DEFAULT_MAX_QUESTIONS_DAILY) 

    today = datetime.now().date()
    
    # معالجة التاريخ والعداد
    last_date = user_data.get('last_use_date')
    if isinstance(last_date, str):
        try:
            last_date = datetime.strptime(last_date.split()[0], '%Y-%m-%d').date()
        except:
            last_date = datetime.min.date()
    elif last_date is None:
        last_date = datetime.min.date()
    elif isinstance(last_date, pd.Timestamp):
        last_date = last_date.date()
        
    current_used = user_data.get('questions_used', 0)
    
    if last_date != today:
        current_used = 0
    
    new_used = current_used
    can_use = True
    
    try:
        with postgres_conn.session as session: # ✅ استخدام session هنا
            if increment and current_used < user_limit:
                new_used = current_used + 1
                session.execute(f"""
                    UPDATE {USER_TABLE} SET questions_used = %s, last_use_date = %s WHERE email = %s
                """, params=(new_used, today, email))
                session.commit()
                
            elif increment and current_used >= user_limit:
                can_use = False
            
            elif not increment and last_date != today:
                # إعادة تعيين العداد عند أول استخدام في يوم جديد
                session.execute(f"""
                    UPDATE {USER_TABLE} SET questions_used = 0, last_use_date = %s WHERE email = %s
                """, params=(today, email))
                session.commit()
                new_used = 0
            
    except Exception as e:
        st.error(f"خطأ في تحديث الاستخدام: {e}")
        return False, current_used, user_limit 
        
    return can_use, new_used, user_limit 
    
# -------------------- 5. دوال عرض نماذج التسجيل والدخول --------------------

def login_form():
    """عرض نموذج تسجيل الدخول."""
    with st.form("login_form"):
        st.subheader("تسجيل الدخول")
        email = st.text_input("البريد الإلكتروني").strip()
        password = st.text_input("كلمة المرور", type="password")
        submitted = st.form_submit_button("تسجيل الدخول")

        if submitted and postgres_conn:
            user_data = postgres_get_user_data(email) 
            
            if user_data:
                hashed_password = user_data.get('password_hash', '').encode('utf-8')
                if bcrypt.checkpw(password.encode('utf-8'), hashed_password): 
                    st.session_state['logged_in'] = True
                    st.session_state['user_email'] = email
                    st.success("تم تسجيل الدخول بنجاح! 🥳")
                    st.rerun() 
                else:
                    st.error("خطأ في البريد الإلكتروني أو كلمة المرور.")
            else:
                st.error("خطأ في البريد الإلكتروني أو كلمة المرور.")
        elif submitted and postgres_conn is None:
            st.error("تعذّر تسجيل الدخول بسبب خطأ في اتصال قاعدة البيانات. يُرجى مراجعة الشريط الجانبي.")

def register_form():
    """عرض نموذج تسجيل حساب جديد."""
    with st.form("register_form"):
        st.subheader("إنشاء حساب جديد")
        email = st.text_input("البريد الإلكتروني").strip()
        password = st.text_input("كلمة المرور", type="password")
        
        grades = [
            "السنة الأولى إعدادي", "السنة الثانية إعدادي", "السنة الثالثة إعدادي",
            "الجذع المشترك العلمي", "الأولى بكالوريا (علوم تجريبية)", 
            "الأولى بكالوريا (علوم رياضية)", "الثانية بكالوريا (علوم فيزيائية)", 
            "الثانية بكالوريا (علوم الحياة والأرض)", "الثانية بكالوريا (علوم رياضية)",
            "غير ذلك (جامعة/آداب/تكوين مهني)"
        ]
        grade = st.selectbox("المستوى الدراسي (النظام المغربي)", grades)
        
        submitted = st.form_submit_button("تسجيل الحساب")

        if submitted and postgres_conn:
            if not email or not password or len(password) < 6:
                st.error("الرجاء إدخال بيانات صالحة وكلمة مرور لا تقل عن 6 أحرف.")
                return

            if postgres_add_user(email, password, grade):
                st.success("تم التسجيل بنجاح! يمكنك الآن تسجيل الدخول.")
            else:
                st.error("البريد الإلكتروني مُسجل بالفعل. حاول تسجيل الدخول.")
        elif submitted and postgres_conn is None:
            st.error("تعذّر التسجيل بسبب خطأ في اتصال قاعدة البيانات. يُرجى مراجعة الشريط الجانبي.")

# -------------------- 6. لوحة تحكم المسؤول --------------------

def admin_dashboard_ui():
    """واجهة لوحة تحكم المسؤول لإدارة مستويات اشتراك المستخدمين."""
    st.title("🛡️ لوحة تحكم المسؤول")
    
    if postgres_conn is None:
        st.error("تعذّر عرض اللوحة: لا يمكن الاتصال بقاعدة البيانات.")
        return
        
    st.subheader("إدارة قيود المستخدمين (مستويات الاشتراك)")
    
    try:
        # جلب جميع المستخدمين
        # نستخدم query لأنها لعمليات SELECT التي لا تغير البيانات
        rows_df = postgres_conn.query(f"SELECT email, school_grade, questions_used, tier FROM {USER_TABLE}", ttl=0)
        
        if rows_df.empty:
             users_data = []
        else:
             users_data = rows_df.to_dict('records')
            
    except Exception as e:
        st.error(f"فشل جلب بيانات المستخدمين: {e}")
        return

    if not users_data:
        st.info("لا يوجد مستخدمون مسجلون بعد.")
        return

    st.write("---")
    for data in users_data:
        email = data['email']
        current_tier = data.get('tier', 'Free')
        current_limit = USER_TIERS.get(current_tier, DEFAULT_MAX_QUESTIONS_DAILY)

        with st.container(border=True):
            st.markdown(f"**البريد الإلكتروني:** `{email}`")
            st.markdown(f"**المستوى الدراسي:** {data.get('school_grade', 'غير محدد')}")
            st.markdown(f"**الاستخدام الحالي اليومي:** {data.get('questions_used', 0)} / **{current_limit}**")

            with st.form(f"tier_update_form_{email}", clear_on_submit=False):
                new_tier = st.selectbox(
                    "تغيير مستوى الاشتراك",
                    options=list(USER_TIERS.keys()),
                    index=list(USER_TIERS.keys()).index(current_tier),
                    key=f"tier_select_{email}"
                )
                submitted = st.form_submit_button("تحديث المستوى")

                if submitted:
                    try:
                        # ✅ استخدام session.execute
                        with postgres_conn.session as session:
                            session.execute(f"""
                                UPDATE {USER_TABLE} SET tier = %s WHERE email = %s
                            """, params=(new_tier, email))
                            session.commit()
                        st.success(f"تم تحديث مستوى المستخدم {email} إلى **{new_tier}**.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"فشل التحديث: {e}")


# -------------------- 7. دالة واجهة التطبيق الرئيسية (Main UI) --------------------

def main_app_ui():
    """عرض واجهة التطبيق الرئيسية (حل المسائل) والتحكم بالتقييد والتخصيص."""
    
    st.title("🇲🇦 حلول المسائل بالذكاء الاصطناعي")
    st.caption("يرجى التأكد من تحميل صورة عالية الجودة مع نص واضح وتمرين واحد")
    
    # 1. تحديث العداد وعرض حالة الاستخدام
    can_use, current_used, user_limit = postgres_update_usage(st.session_state['user_email'])
    
    st.info(f"الأسئلة اليومية المتبقية: {user_limit - current_used} من {user_limit}.")
    
    if current_used >= user_limit:
        st.error(f"لقد استنفدت الحد الأقصى ({user_limit}) من الأسئلة لهذا اليوم. يرجى العودة غداً.")
        st.stop()

    # 2. منطق رفع الصورة والحل
    uploaded_file = st.file_uploader("قم بتحميل صورة المسألة", type=["png", "jpg", "jpeg"])

    if uploaded_file is not None:
        image = Image.open(uploaded_file)
        st.image(image, caption='صورة المسألة.', use_column_width=True)
        
        if st.button("🚀 ابدأ الحل والتحليل"):
            
            if st.session_state.gemini_client is None:
                st.error("تعذرت معالجة الطلب: تهيئة Gemini Client فشلت. راجع الشريط الجانبي.")
                st.stop()
            
            with st.spinner('يتم تحليل الصورة وتقديم الحل...'):
                try:
                    
                    # 1. قراءة تعليمات النظام من ملف system_prompt.txt
                    try:
                        with open("system_prompt.txt", "r", encoding="utf-8") as f:
                            SYSTEM_PROMPT = f.read()
                    except FileNotFoundError:
                        st.error("❌ لم يتم العثور على ملف system_prompt.txt. تأكد من وجوده في نفس المجلد.")
                        st.stop()
                        
                    full_user_data = postgres_get_user_data(st.session_state['user_email'])
                    user_grade = full_user_data.get('school_grade', 'مستوى غير محدد')
                    
                    custom_prompt = (
                        f"{SYSTEM_PROMPT}\n"
                        f"مستوى الطالب هو: {user_grade}. يجب أن يكون الحل المفصل المقدم مناسبًا تمامًا لهذا المستوى التعليمي المحدد في النظام المغربي، مع التركيز على المنهجيات التي تدرس في هذا المستوى."
                    )
                    
                    contents = [custom_prompt, image]

                    response = st.session_state.gemini_client.models.generate_content(
                        model='gemini-2.5-flash', 
                        contents=contents
                    )
                    
                    # 3. تحديث الاستخدام بعد نجاح الحل
                    postgres_update_usage(st.session_state['user_email'], increment=True) 
                    
                    st.success("تم تحليل وحل المسألة بنجاح! 🎉")
                    st.subheader("📝 الحل المفصل")
                    st.markdown(response.text)
                    st.rerun() # لإعادة تحميل حالة الاستخدام
                    
                except Exception as e:
                    st.error(f"حدث خطأ أثناء الاتصال بالنموذج أو قاعدة البيانات: {e}")
                        
# -------------------- 8. المنطق الرئيسي للتطبيق --------------------

if 'logged_in' not in st.session_state:
    st.session_state['logged_in'] = False
if 'user_email' not in st.session_state:
    st.session_state['user_email'] = None

# 2. عرض حالة النظام في الشريط الجانبي
st.sidebar.header("حالة النظام")
st.sidebar.markdown(st.session_state.db_status)
st.sidebar.markdown(st.session_state.gemini_status)

if st.session_state['logged_in']:
    if st.session_state['user_email'] == ADMIN_EMAIL:
        admin_dashboard_ui()
    else:
        main_app_ui()
else:
    st.header("أهلاً بك يا أحمد في منصة Math AI zaoujal")
    st.subheader(f"الرجاء تسجيل الدخول أو إنشاء حساب لاستخدام خدمة حل المسائل")
    login_tab, register_tab = st.tabs(["تسجيل الدخول", "إنشاء حساب"])
    
    with login_tab:
        login_form()
    
    with register_tab:
        register_form()
