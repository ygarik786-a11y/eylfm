"""Сквозной тест E.Y.L FM через Flask test client."""
import os, re, sqlite3, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('EYLFM_SECRET', 'test-secret')
import app as appmod

# чистая тестовая база
if os.path.exists(appmod.DB_PATH):
    os.remove(appmod.DB_PATH)
appmod.init_db()

client = appmod.app.test_client()

def token(html):
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, 'no csrf token in page'
    return m.group(1)

def post(path, data, follow=True, token_from=None):
    get = client.get(token_from or path)
    t = token(get.data.decode())
    data = dict(data); data['csrf_token'] = t
    return client.post(path, data=data, follow_redirects=follow)

def check(cond, label):
    print(('OK  ' if cond else 'FAIL'), label)
    assert cond, label

# 1. регистрация erik (первый = админ)
r = post('/register', {'username': 'erik', 'name': 'Ерик Лебедь', 'password': 'test1234', 'password2': 'test1234'})
check(r.status_code == 200 and 'Редактирование профиля' in r.data.decode() or 'Ерик' in r.data.decode(), 'регистрация erik')
db = sqlite3.connect(appmod.DB_PATH); db.row_factory = sqlite3.Row
check(db.execute("SELECT is_admin FROM users WHERE username='erik'").fetchone()[0] == 1, 'первый пользователь — админ')
check(db.execute("SELECT COUNT(*) FROM gifts").fetchone()[0] == 12, 'каталог подарков: 12')

# 2. регистрация mita
client.get('/logout')
r = post('/register', {'username': 'mita', 'name': 'Мита Лебедь', 'password': 'test1234', 'password2': 'test1234'})
check('Редактирование профиля' in r.data.decode() or r.status_code == 200, 'регистрация mita')

# 3. mita задаёт erik вопрос (не анонимно) + анонимный
r = post('/ask', {'to': 'erik', 'text': 'Какой твой любимый фильм?'}, token_from='/u/erik')
check('Вопрос отправлен' in r.data.decode(), 'вопрос задан')
r = post('/ask', {'to': 'erik', 'text': 'Ты любишь зиму?', 'anon': '1'}, token_from='/u/erik')
check(db.execute("SELECT COUNT(*) FROM questions WHERE to_id=1").fetchone()[0] == 2, 'два вопроса у erik')

# 4. mita подписывается на erik и лайкает пока нечего; сначала erik отвечает
client.get('/logout')
r = post('/login', {'username': 'erik', 'password': 'test1234'})
check('Выйти' in r.data.decode(), 'вход erik')
r = client.get('/inbox')
check('Какой твой любимый фильм?' in r.data.decode(), 'вопрос виден во входящих')
qid = db.execute("SELECT id FROM questions WHERE text LIKE 'Какой твой%'").fetchone()[0]
r = post('/answer/' + str(qid), {'answer': 'Ворон, конечно!'}, token_from='/inbox')
check('Ответ опубликован' in r.data.decode(), 'ответ опубликован')
check(db.execute("SELECT answer FROM questions WHERE id=?", (qid,)).fetchone()[0] == 'Ворон, конечно!', 'ответ в базе')

# 5. mita лайкает ответ и подписывается
client.get('/logout')
post('/login', {'username': 'mita', 'password': 'test1234'})
r = post('/like/' + str(qid), {}, token_from='/u/erik')
check(db.execute("SELECT COUNT(*) FROM likes WHERE qid=?", (qid,)).fetchone()[0] == 1, 'лайк поставлен')
r = post('/follow/erik', {}, token_from='/u/erik')
check(db.execute("SELECT COUNT(*) FROM follows WHERE follower_id=2 AND followed_id=1").fetchone()[0] == 1, 'подписка оформлена')

# 6. подарок
gift_id = db.execute("SELECT id FROM gifts WHERE name='Роза'").fetchone()[0]
r = post('/gift', {'to': 'erik', 'gift_id': gift_id, 'message': 'С любовью!'}, token_from='/gifts')
check('Подарок' in r.data.decode() and 'отправлен' in r.data.decode(), 'подарок отправлен')
check(db.execute("SELECT COUNT(*) FROM user_gifts WHERE to_id=1").fetchone()[0] == 1, 'подарок в базе')

# 7. лента, профиль, поиск, уведомления
r = client.get('/')
check('Ворон, конечно!' in r.data.decode(), 'ответ в ленте')
r = client.get('/u/erik')
check('Ерик Лебедь' in r.data.decode() and '1' in r.data.decode(), 'профиль erik открывается')
r = client.get('/search?q=erik')
check('Ерик Лебедь' in r.data.decode(), 'поиск находит erik')
r = client.get('/u/erik?tab=gifts')
check('Роза' in r.data.decode(), 'подарок виден в профиле')
client.get('/logout')
post('/login', {'username': 'erik', 'password': 'test1234'})
r = client.get('/notifications')
check('задал вам вопрос' in r.data.decode() or 'подарил' in r.data.decode(), 'уведомления есть')
r = client.get('/liked')
check(r.status_code == 200, 'страница Понравившиеся')
r = client.get('/friends')
check(r.status_code == 200, 'страница Друзья')
r = client.get('/settings')
check('Сменить пароль' in r.data.decode(), 'настройки открываются')

# 8. удаление ответа владельцем
r = post('/delete/' + str(qid), {}, token_from='/u/erik')
check(db.execute("SELECT COUNT(*) FROM questions WHERE id=?", (qid,)).fetchone()[0] == 0, 'ответ удалён')

print('\nВСЕ ПРОВЕРКИ ПРОШЛИ')
