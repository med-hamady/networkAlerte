# تحديث تتبع طلبات القطع وتشخيص الوصول

هذه النسخة تجمع تغييرات الواجهة مع Backend اللازم لها، انطلاقاً من main عند b2c9cf4. لا تنقل ملفات env أو النسخة المحلية أو بيانات الشركة.

## التغييرات

- API `/api/v1/fai-requests` وقراءة أحداث الطلب مع صلاحية `fai.requests.view`.
- دعم `matching` و`offset` في سجل FAI مع المحافظة على قراءة السجل القديم.
- جداول الطلبات والأحداث، ملاحظات الاتصال، فترات الانتظار وهوية الطلب في السجل.
- توحيد تسجيل الطلبات من API ولوحة التحكم وسكربت القطع، وقفل التنفيذ بين القطع وإعادة الخدمة.
- لا تُعتبر أوامر SSH أو MikroTik مؤكدة قبل التحقق من النتيجة. يبقى فشل تنظيف حظر الراوتر في قائمة إعادة المحاولة.
- يُجرّب جهاز العميل أولاً؛ حظر الراوتر يستخدم كحل احتياطي عند الحاجة. هذا تغيير تشغيلي يجب مراجعته مع مسؤول الشبكة.
- البيانات القديمة لا تُملأ بنتائج أو أوقات مفترضة. التتبع المفصل يبدأ من الطلبات الجديدة.
- Diagnostics d'accès يحتفظ بأول وقت رصد شاهده المتصفح في localStorage؛ ليس آخر رصد حي ولا تاريخاً مشتركاً بين المستخدمين.

## الاختبارات

نجح 148 اختباراً في حاوية بلا شبكة، تشمل التنفيذ المتزامن المحاكى وإلغاء الطلب وإعادة الخدمة والسجل والصلاحيات وSSH. عمليات المعدات محاكاة؛ لا يعد ذلك تجربة على معدات الشركة.
نجحت ترحيلات PostgreSQL 16 المعزولة: m2 إلى n3 ثم o4، تكرار upgrade، downgrade تجريبي، ثم upgrade. فُحص الحفاظ على الصفوف القديمة، القيود والفهارس والقيم الافتراضية.
ترحيلات الاختبار لا تستخدم بيانات الشركة. يلزم التحقق من revision الفعلي للسيرفر قبل النشر.

## خطة النشر بعد المراجعة والموافقة

نفذ الأوامر في Bash على السيرفر، داخل مجلد المشروع الصحيح. لا تنفذها من مجلد Windows المحلي.

1. افحص `git status --short`؛ إذا وجدت تغييرات محلية فتوقف لمقارنتها وحفظها. حدد commit النشر بعد مراجعة PR رقم 16، وافحص فرقه مع commit السيرفر. لا تستخدم تحديثاً شاملاً غير مراجع.
2. اضبط نفس ملفات Compose الحالية:

```bash
export LAN_BIND_IP=10.135.3.25
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.lan.yml'
dc ps
dc exec -T backend alembic current
```

المسار المتوقع للترحيل يبدأ من m2c3d4e5f6a7. أي revision مختلف يحتاج مراجعة قبل المتابعة.

3. قبل استبدال المصدر، احفظ commit الحالي وصور Docker ثم أوقف كل خدمات Python التي تستخدم bind mount للمصدر، في نافذة صيانة متفق عليها:

```bash
umask 077
release_backup="$HOME/networkalerte-before-fai-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$release_backup"
git rev-parse HEAD > "$release_backup/commit.txt"
dc images > "$release_backup/images.txt"
dc stop backend scheduler scheduler-heavy scheduler-ping-lr scheduler-poll-switch scheduler-poll-af60 scheduler-poll-ltu scheduler-poll-airos netflow-collector
```

4. احفظ قاعدة البيانات والسجل والأدلة والمصدر والإعدادات بصلاحيات خاصة. لا ترفع النسخة إلى Git:

```bash
dc exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$release_backup/database.dump"
test -s "$release_backup/database.dump"
dc exec -T postgres pg_restore --list < "$release_backup/database.dump" > "$release_backup/database-list.txt"
tar -czf "$release_backup/files.tar.gz" backend frontend nginx .env docker-compose.yml docker-compose.prod.yml docker-compose.lan.yml
```

تحقق من نجاح كل أمر قبل التالي. يجب أن تكون نسخة السجل والأدلة شاملة لمسارات FAI_LOG_PATH وFAI_EVIDENCE_DIR الفعلية، حتى إذا كانت خارج backend. فحص قائمة dump ليس اختبار استعادة كاملاً؛ يلزم تجربة استعادة في قاعدة منفصلة وفق سياسة الشركة.

5. بعد تثبيت commit المراجع فقط مع المحافظة على ملفات env، ابنِ الصور. تغيّر requirements لإضافة اعتماد اختبارات؛ يلزم اتساق صور خدمات Python. لا تُشغّل نسخة جديدة قبل نجاح البناء:

```bash
dc build backend scheduler scheduler-heavy scheduler-ping-lr scheduler-poll-switch scheduler-poll-af60 scheduler-poll-ltu scheduler-poll-airos netflow-collector frontend
dc run --rm --no-deps --entrypoint alembic backend upgrade head
```

إذا فشل الترحيل فتوقف وافحص الخطأ. لا تبدأ schedulers ولا تُكرر تغييرات قاعدة البيانات عشوائياً.

6. بعد نجاح الترحيل، شغّل API وافحص الصحة وrevision ثم الواجهة ثم المجدولات:

```bash
dc up -d --no-deps backend
dc logs --tail=100 backend
dc exec -T backend alembic current
dc exec -T backend python scripts/check_company_database.py
```

انتظر صحة API وrevision o4e5f6a7b8c9 قبل المتابعة:

```bash
dc up -d --no-deps frontend
dc up -d --no-deps scheduler scheduler-heavy scheduler-ping-lr scheduler-poll-switch scheduler-poll-af60 scheduler-poll-ltu scheduler-poll-airos netflow-collector
dc ps
```

افحص الصفحتين بحساب مخوّل، تحميل الصفحات والبيانات والسجل القديم. لا تستخدم قطع عميل حقيقي لاختبار العرض. إذا أعاد nginx خطأ 502 بعد تغير عنوان الحاوية، افحص إعداداته ثم أعد تحميله.

## الرجوع

إذا فشل البناء قبل الترحيل، أعد المصدر والصور السابقة ثم الخدمات. بعد نجاح الترحيل، يمكن إعادة الكود القديم مع إبقاء الأعمدة والجداول الإضافية؛ اختبر ذلك قبل اعتماده. لا تنفذ downgrade على قاعدة الإنتاج بعد تسجيل طلبات، لأنه يحذف التاريخ الجديد. استعادة dump تستبدل البيانات وتتطلب قراراً صريحاً وخطة لحفظ ما حدث بعد النسخة. لا تستخدم down -v أو prune.

## القيود المتبقية

لم تُنفذ هذه الخطة على السيرفر. يلزم فحص revision والنسخ الاحتياطية وحالة الخدمات الحية ومراجعة مسؤول الشبكة للسلوك التشغيلي قبل التنفيذ.
