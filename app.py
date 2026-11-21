# E.Y.L FM — соцсеть вопросов и ответов в духе старого ASK.fm (2013)
# Flask + SQLite. Один файл, без платных API. Запуск: venv/bin/python app.py
import os, re, sqlite3, secrets, time
from datetime import datetime
from flask import Flask, g, render_template, request, redirect, url_for, session, flash, abort, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'eylfm.db')
AVATAR_DIR = os.path.join(BASE_DIR, 'static', 'avatars')
os.makedirs(AVATAR_DIR, exist_ok=True)
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif'}
MAX_AVATAR = 2 * 1024 * 1024

app = Flask(__name__)
app.secret_key = os.environ.get('EYLFM_SECRET', 'eylfm-dev-secret-change-me')
app.config['MAX_CONTENT_LENGTH'] = MAX_AVATAR + 1024

GIFTS = [
    ('🌹', 'Роза', 'Классическая красная роза'), ('💛', 'Жёлтое сердце', 'Тёплое жёлтое сердце'),
    ('🎁', 'Подарок', 'Коробка с сюрпризом'), ('🍓', 'Клубника', 'Сладкая клубника'),
    ('⭐', 'Звезда', 'Яркая звезда'), ('🎄', 'Ёлочка', 'Новогодняя ёлочка'),
    ('🐱', 'Котик', 'Милый котик'), ('🦁', 'Лев', 'Царь зверей'),
    ('💎', 'Бриллиант', 'Сияет ярче всех'), ('🎸', 'Гитара', 'Для меломанов'),
    ('☕', 'Чашка кофе', 'Тёплый кофе'), ('🌙', 'Луна', 'Тихая ночь'),
]

# ---------- база ----------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute('PRAGMA foreign_keys = ON')
    return g.db

@app.teardown_appcontext
def close_db(exc=None):
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL, name TEXT NOT NULL, bio TEXT DEFAULT '',
      city TEXT DEFAULT '', avatar TEXT DEFAULT '', created_at INTEGER NOT NULL,
      is_admin INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS questions(
      id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER, to_id INTEGER NOT NULL,
      text TEXT NOT NULL, is_anon INTEGER DEFAULT 0, created_at INTEGER NOT NULL,
      answer TEXT, answered_at INTEGER,
      FOREIGN KEY(from_id) REFERENCES users(id), FOREIGN KEY(to_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS likes(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, qid INTEGER NOT NULL,
      created_at INTEGER NOT NULL, UNIQUE(user_id, qid),
      FOREIGN KEY(user_id) REFERENCES users(id), FOREIGN KEY(qid) REFERENCES questions(id));
    CREATE TABLE IF NOT EXISTS follows(
      id INTEGER PRIMARY KEY AUTOINCREMENT, follower_id INTEGER NOT NULL, followed_id INTEGER NOT NULL,
      created_at INTEGER NOT NULL, UNIQUE(follower_id, followed_id),
      FOREIGN KEY(follower_id) REFERENCES users(id), FOREIGN KEY(followed_id) REFERENCES users(id));
    CREATE TABLE IF NOT EXISTS gifts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, emoji TEXT NOT NULL, descr TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS user_gifts(
      id INTEGER PRIMARY KEY AUTOINCREMENT, from_id INTEGER NOT NULL, to_id INTEGER NOT NULL,
      gift_id INTEGER NOT NULL, message TEXT DEFAULT '', created_at INTEGER NOT NULL,
      FOREIGN KEY(from_id) REFERENCES users(id), FOREIGN KEY(to_id) REFERENCES users(id),
      FOREIGN KEY(gift_id) REFERENCES gifts(id));
    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
      text TEXT NOT NULL, link TEXT DEFAULT '', is_read INTEGER DEFAULT 0, created_at INTEGER NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id));
    ''')
    if db.execute('SELECT COUNT(*) FROM gifts').fetchone()[0] == 0:
        db.executemany('INSERT INTO gifts(name, emoji, descr) VALUES(?,?,?)',
                       [(n, e, d) for (e, n, d) in GIFTS])
    # миграции для админки (2026-11-21): бан и анонимные подарки
    ucols = [r[1] for r in db.execute('PRAGMA table_info(users)').fetchall()]
    if 'is_banned' not in ucols:
        db.execute('ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0')
    gcols = [r[1] for r in db.execute('PRAGMA table_info(user_gifts)').fetchall()]
    if 'is_anon' not in gcols:
        db.execute('ALTER TABLE user_gifts ADD COLUMN is_anon INTEGER DEFAULT 0')
    db.execute("UPDATE users SET is_admin=1 WHERE username='erik'")
    db.commit(); db.close()

init_db()

# ---------- помощники ----------
def now():
    return int(time.time())

def timeago(ts):
    d = now() - int(ts)
    if d < 60: return 'только что'
    if d < 3600: return f'{d//60} мин. назад'
    if d < 86400: return f'{d//3600} ч. назад'
    if d < 172800: return 'вчера'
    if d < 2592000: return f'{d//86400} дн. назад'
    return datetime.fromtimestamp(int(ts)).strftime('%d.%m.%Y')

app.jinja_env.filters['timeago'] = timeago

def current_user():
    uid = session.get('user_id')
    if not uid: return None
    return get_db().execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def w(*a, **kw):
        if not current_user():
            flash('Сначала войдите или зарегистрируйтесь.', 'err')
            return redirect(url_for('login'))
        return fn(*a, **kw)
    return w

def csrf_token():
    if 'csrf' not in session:
        session['csrf'] = secrets.token_hex(16)
    return session['csrf']

app.jinja_env.globals['csrf_token'] = csrf_token

@app.before_request
def check_csrf():
    if request.method == 'POST':
        if request.form.get('csrf_token') != session.get('csrf'):
            abort(400, 'Неверный токен формы. Обновите страницу.')

def notify(user_id, kind, text, link=''):
    get_db().execute('INSERT INTO notifications(user_id, kind, text, link, created_at) VALUES(?,?,?,?,?)',
                     (user_id, kind, text, link, now()))
    get_db().commit()

def unread_count(uid):
    return get_db().execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND is_read=0', (uid,)).fetchone()[0]

def valid_username(u):
    return bool(re.match(r'^[a-zA-Z0-9_]{3,20}$', u or ''))

@app.context_processor
def inject_common():
    u = current_user()
    return dict(me=u, unread=unread_count(u['id']) if u else 0,
                all_users_count=get_db().execute('SELECT COUNT(*) FROM users').fetchone()[0])

# ---------- главная и авторизация ----------
@app.route('/')
def index():
    db = get_db()
    me = current_user()
    if me:
        ids = [r[0] for r in db.execute('SELECT followed_id FROM follows WHERE follower_id=?', (me['id'],)).fetchall()]
        ids.append(me['id'])
        q = 'SELECT q.*, u.username, u.name, u.avatar FROM questions q JOIN users u ON u.id=q.to_id WHERE q.answer IS NOT NULL AND q.to_id IN (%s) ORDER BY q.answered_at DESC LIMIT 30' % ','.join('?'*len(ids))
        feed = db.execute(q, ids).fetchall()
    else:
        feed = db.execute('SELECT q.*, u.username, u.name, u.avatar FROM questions q JOIN users u ON u.id=q.to_id WHERE q.answer IS NOT NULL ORDER BY q.answered_at DESC LIMIT 30').fetchall()
    liked = set()
    if me:
        liked = {r[0] for r in db.execute('SELECT qid FROM likes WHERE user_id=?', (me['id'],)).fetchall()}
    people = db.execute('SELECT * FROM users ORDER BY created_at DESC LIMIT 6').fetchall()
    import datetime as _dt
    _dq = ['Какой фильм ты можешь пересматривать вечно? 🎬',
           'Какая песня у тебя сейчас на повторе? 🎵',
           'Если бы у тебя была суперспособность — какая? ✨',
           'Твой любимый вкус мороженого? 🍦',
           'В каком городе ты мечтаешь побывать? 🌍',
           'Какая игра из детства — самая любимая? 🎮',
           'Кофе или чай? ☕',
           'Какой подарок ты запомнил на всю жизнь? 🎁',
           'Если бы животные умели говорить — с кем бы ты поговорил первым? 🐶',
           'Твоё идеальное воскресенье — какое оно? 🌤️',
           'Какая еда — самая вкусная на свете? 🍕',
           'Что ты умеешь делать лучше всех? 😎',
           'Какой праздник ждёшь больше всего в году? 🎄',
           'Если бы ночью совсем не хотелось спать — чем бы ты занялся? 🌙']
    day_q = _dq[_dt.date.today().timetuple().tm_yday % len(_dq)]
    return render_template('index.html', feed=feed, liked=liked, people=people, day_q=day_q)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        name = (request.form.get('name') or '').strip()[:50]
        pw = request.form.get('password') or ''
        pw2 = request.form.get('password2') or ''
        db = get_db()
        if not valid_username(username):
            flash('Ник: только латинские буквы, цифры и _, от 3 до 20 символов.', 'err')
        elif not name:
            flash('Укажите имя.', 'err')
        elif len(pw) < 4:
            flash('Пароль — минимум 4 символа.', 'err')
        elif pw != pw2:
            flash('Пароли не совпадают.', 'err')
        elif db.execute('SELECT id FROM users WHERE username=?', (username,)).fetchone():
            flash('Такой ник уже занят.', 'err')
        else:
            is_admin = 1 if db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0 else 0
            cur = db.execute('INSERT INTO users(username, password_hash, name, created_at, is_admin) VALUES(?,?,?,?,?)',
                             (username, generate_password_hash(pw), name, now(), is_admin))
            db.commit()
            session['user_id'] = cur.lastrowid
            flash('Добро пожаловать в E.Y.L FM, ' + name + '! Заполните профиль.', 'ok')
            return redirect(url_for('edit_profile'))
        return render_template('auth.html', mode='register')
    return render_template('auth.html', mode='register')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip().lower()
        pw = request.form.get('password') or ''
        u = get_db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        if u and u['is_banned']:
            flash('Этот аккаунт заблокирован администратором.', 'err')
        elif u and check_password_hash(u['password_hash'], pw):
            session['user_id'] = u['id']
            return redirect(url_for('index'))
        else:
            flash('Неверный ник или пароль.', 'err')
    return render_template('auth.html', mode='login')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# ---------- профиль ----------
@app.route('/u/<username>')
def profile(username):
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE username=?', (username.lower(),)).fetchone()
    if not u: abort(404)
    tab = request.args.get('tab', 'answers')
    me = current_user()
    answers = db.execute('SELECT q.* FROM questions q WHERE q.to_id=? AND q.answer IS NOT NULL ORDER BY q.answered_at DESC LIMIT 50', (u['id'],)).fetchall()
    stats = {
        'answers': db.execute('SELECT COUNT(*) FROM questions WHERE to_id=? AND answer IS NOT NULL', (u['id'],)).fetchone()[0],
        'likes': db.execute('SELECT COUNT(*) FROM likes l JOIN questions q ON q.id=l.qid WHERE q.to_id=?', (u['id'],)).fetchone()[0],
        'followers': db.execute('SELECT COUNT(*) FROM follows WHERE followed_id=?', (u['id'],)).fetchone()[0],
        'following': db.execute('SELECT COUNT(*) FROM follows WHERE follower_id=?', (u['id'],)).fetchone()[0],
    }
    liked = set()
    if me:
        liked = {r[0] for r in db.execute('SELECT qid FROM likes WHERE user_id=?', (me['id'],)).fetchall()}
    is_following = bool(me and db.execute('SELECT id FROM follows WHERE follower_id=? AND followed_id=?', (me['id'], u['id'])).fetchone())
    my_gifts = db.execute('''SELECT ug.*, g.emoji, g.name AS gname,
        CASE WHEN ug.is_anon=1 THEN '' ELSE us.username END AS from_username,
        CASE WHEN ug.is_anon=1 THEN 'Аноним' ELSE us.name END AS from_name
        FROM user_gifts ug JOIN gifts g ON g.id=ug.gift_id JOIN users us ON us.id=ug.from_id
        WHERE ug.to_id=? ORDER BY ug.created_at DESC LIMIT 30''', (u['id'],)).fetchall()
    return render_template('profile.html', u=u, tab=tab, answers=answers, stats=stats,
                           liked=liked, is_following=is_following, my_gifts=my_gifts)

@app.route('/my')
@login_required
def my_page():
    return redirect(url_for('profile', username=current_user()['username']))

# ---------- вопросы ----------
@app.route('/ask', methods=['POST'])
def ask():
    to_username = (request.form.get('to') or '').strip().lower()
    text = (request.form.get('text') or '').strip()[:300]
    anon = 1 if request.form.get('anon') else 0
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE username=?', (to_username,)).fetchone()
    if not u:
        flash('Такого пользователя нет.', 'err'); return redirect(url_for('index'))
    if not text:
        flash('Напишите вопрос.', 'err'); return redirect(url_for('profile', username=to_username))
    me = current_user()
    if anon and not me:
        from_id = None
    else:
        from_id = me['id'] if me else None
        if anon: from_id = None
    db.execute('INSERT INTO questions(from_id, to_id, text, is_anon, created_at) VALUES(?,?,?,?,?)',
               (from_id, u['id'], text, 1 if (anon or not me) else 0, now()))
    db.commit()
    who = 'Аноним' if (anon or not me) else me['name']
    notify(u['id'], 'question', f'{who} задал вам вопрос: «{text[:80]}»', url_for('inbox'))
    flash('Вопрос отправлен!', 'ok')
    return redirect(url_for('profile', username=to_username))

@app.route('/inbox')
@login_required
def inbox():
    me = current_user()
    qs = get_db().execute('''SELECT q.*, fu.username AS from_username, fu.name AS from_name
        FROM questions q LEFT JOIN users fu ON fu.id=q.from_id
        WHERE q.to_id=? AND q.answer IS NULL ORDER BY q.created_at DESC LIMIT 50''', (me['id'],)).fetchall()
    return render_template('inbox.html', qs=qs)

@app.route('/answer/<int:qid>', methods=['POST'])
@login_required
def answer(qid):
    me = current_user()
    db = get_db()
    q = db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    if not q or q['to_id'] != me['id']: abort(403)
    text = (request.form.get('answer') or '').strip()[:2000]
    if not text:
        flash('Напишите ответ.', 'err'); return redirect(url_for('inbox'))
    db.execute('UPDATE questions SET answer=?, answered_at=? WHERE id=?', (text, now(), qid))
    db.commit()
    if q['from_id']:
        notify(q['from_id'], 'answer', f"{me['name']} ответил на ваш вопрос: «{q['text'][:80]}»", url_for('profile', username=me['username']))
    flash('Ответ опубликован!', 'ok')
    return redirect(url_for('inbox'))

@app.route('/delete/<int:qid>', methods=['POST'])
@login_required
def delete_q(qid):
    me = current_user()
    db = get_db()
    q = db.execute('SELECT * FROM questions WHERE id=?', (qid,)).fetchone()
    if not q or (q['to_id'] != me['id'] and q['from_id'] != me['id']): abort(403)
    db.execute('DELETE FROM likes WHERE qid=?', (qid,))
    db.execute('DELETE FROM questions WHERE id=?', (qid,))
    db.commit()
    flash('Удалено.', 'ok')
    return redirect(request.referrer or url_for('index'))

# ---------- лайки и подписки ----------
@app.route('/like/<int:qid>', methods=['POST'])
@login_required
def like(qid):
    me = current_user()
    db = get_db()
    q = db.execute('SELECT * FROM questions WHERE id=? AND answer IS NOT NULL', (qid,)).fetchone()
    if not q: abort(404)
    row = db.execute('SELECT id FROM likes WHERE user_id=? AND qid=?', (me['id'], qid)).fetchone()
    if row:
        db.execute('DELETE FROM likes WHERE id=?', (row['id'],))
    else:
        db.execute('INSERT INTO likes(user_id, qid, created_at) VALUES(?,?,?)', (me['id'], qid, now()))
        if q['to_id'] != me['id']:
            notify(q['to_id'], 'like', f"{me['name']} понравился ваш ответ: «{q['answer'][:80]}»", url_for('profile', username=me['username']))
    db.commit()
    return redirect(request.referrer or url_for('index'))

@app.route('/follow/<username>', methods=['POST'])
@login_required
def follow(username):
    me = current_user()
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE username=?', (username.lower(),)).fetchone()
    if not u or u['id'] == me['id']: abort(404)
    row = db.execute('SELECT id FROM follows WHERE follower_id=? AND followed_id=?', (me['id'], u['id'])).fetchone()
    if row:
        db.execute('DELETE FROM follows WHERE id=?', (row['id'],))
        flash(f"Вы отписались от {u['name']}.", 'ok')
    else:
        db.execute('INSERT INTO follows(follower_id, followed_id, created_at) VALUES(?,?,?)', (me['id'], u['id'], now()))
        notify(u['id'], 'follow', f"{me['name']} подписался на вас.", url_for('profile', username=me['username']))
        flash(f"Вы подписались на {u['name']}.", 'ok')
    db.commit()
    return redirect(request.referrer or url_for('profile', username=username))

@app.route('/friends')
@login_required
def friends():
    me = current_user()
    db = get_db()
    rows = db.execute('''SELECT u.* FROM follows f JOIN users u ON u.id=f.followed_id
        WHERE f.follower_id=? ORDER BY f.created_at DESC''', (me['id'],)).fetchall()
    return render_template('people.html', title='Друзья — мои подписки', users=rows, empty='Вы пока ни на кого не подписаны.')

@app.route('/followers')
@login_required
def followers():
    me = current_user()
    db = get_db()
    rows = db.execute('''SELECT u.* FROM follows f JOIN users u ON u.id=f.follower_id
        WHERE f.followed_id=? ORDER BY f.created_at DESC''', (me['id'],)).fetchall()
    return render_template('people.html', title='Подписчики', users=rows, empty='У вас пока нет подписчиков.')

@app.route('/liked')
@login_required
def liked():
    me = current_user()
    db = get_db()
    rows = db.execute('''SELECT q.*, u.username, u.name, u.avatar FROM likes l
        JOIN questions q ON q.id=l.qid JOIN users u ON u.id=q.to_id
        WHERE l.user_id=? ORDER BY l.created_at DESC LIMIT 50''', (me['id'],)).fetchall()
    liked = {r['id'] for r in rows}
    return render_template('index.html', feed=rows, liked=liked, people=[], page_title='Понравившиеся ответы')

# ---------- подарки ----------
@app.route('/gifts')
@login_required
def gifts_page():
    me = current_user()
    db = get_db()
    catalog = db.execute('SELECT * FROM gifts ORDER BY id').fetchall()
    mine = db.execute('''SELECT ug.*, g.emoji, g.name AS gname,
        CASE WHEN ug.is_anon=1 THEN '' ELSE us.username END AS from_username,
        CASE WHEN ug.is_anon=1 THEN 'Аноним' ELSE us.name END AS from_name
        FROM user_gifts ug JOIN gifts g ON g.id=ug.gift_id JOIN users us ON us.id=ug.from_id
        WHERE ug.to_id=? ORDER BY ug.created_at DESC LIMIT 30''', (me['id'],)).fetchall()
    users = db.execute('SELECT username, name FROM users WHERE id!=? ORDER BY name LIMIT 100', (me['id'],)).fetchall()
    return render_template('gifts.html', catalog=catalog, mine=mine, users=users)

@app.route('/gift', methods=['POST'])
@login_required
def send_gift():
    me = current_user()
    db = get_db()
    to_username = (request.form.get('to') or '').strip().lower()
    gift_id = request.form.get('gift_id', type=int)
    message = (request.form.get('message') or '').strip()[:140]
    u = db.execute('SELECT * FROM users WHERE username=?', (to_username,)).fetchone()
    g = db.execute('SELECT * FROM gifts WHERE id=?', (gift_id,)).fetchone()
    if not u or not g:
        flash('Выберите получателя и подарок.', 'err'); return redirect(url_for('gifts_page'))
    db.execute('INSERT INTO user_gifts(from_id, to_id, gift_id, message, created_at) VALUES(?,?,?,?,?)',
               (me['id'], u['id'], gift_id, message, now()))
    db.commit()
    notify(u['id'], 'gift', f"{me['name']} подарил вам «{g['name']}» {g['emoji']}", url_for('profile', username=u['username']) + '?tab=gifts')
    flash(f"Подарок «{g['name']}» отправлен {u['name']}!", 'ok')
    return redirect(url_for('gifts_page'))

# ---------- админка создателя (2026-11-21, слово Erik) ----------
def admin_required(fn):
    from functools import wraps
    @wraps(fn)
    def w(*a, **kw):
        u = current_user()
        if not u or not u['is_admin']:
            abort(404)
        return fn(*a, **kw)
    return w

@app.route('/admin')
@admin_required
def admin_panel():
    db = get_db()
    stats = {
        'users': db.execute('SELECT COUNT(*) FROM users').fetchone()[0],
        'questions': db.execute('SELECT COUNT(*) FROM questions').fetchone()[0],
        'answers': db.execute('SELECT COUNT(*) FROM questions WHERE answer IS NOT NULL').fetchone()[0],
        'gifts': db.execute('SELECT COUNT(*) FROM user_gifts').fetchone()[0],
        'likes': db.execute('SELECT COUNT(*) FROM likes').fetchone()[0],
    }
    users = db.execute('SELECT * FROM users ORDER BY id').fetchall()
    catalog = db.execute('SELECT * FROM gifts ORDER BY id').fetchall()
    recent_answers = db.execute('''SELECT q.*, tu.username AS to_username, tu.name AS to_name
        FROM questions q JOIN users tu ON tu.id=q.to_id
        WHERE q.answer IS NOT NULL ORDER BY q.answered_at DESC LIMIT 15''').fetchall()
    recent_questions = db.execute('''SELECT q.*, tu.username AS to_username, tu.name AS to_name
        FROM questions q JOIN users tu ON tu.id=q.to_id
        WHERE q.answer IS NULL ORDER BY q.created_at DESC LIMIT 15''').fetchall()
    return render_template('admin.html', stats=stats, users=users, catalog=catalog,
                           recent_answers=recent_answers, recent_questions=recent_questions)

@app.route('/admin/gift_add', methods=['POST'])
@admin_required
def admin_gift_add():
    emoji = (request.form.get('emoji') or '').strip()[:8]
    name = (request.form.get('name') or '').strip()[:40]
    descr = (request.form.get('descr') or '').strip()[:80]
    if emoji and name:
        db = get_db()
        db.execute('INSERT INTO gifts(name, emoji, descr) VALUES(?,?,?)', (name, emoji, descr))
        db.commit()
        flash(f'Подарок «{name}» добавлен в каталог!', 'ok')
    else:
        flash('Укажите эмодзи и название подарка.', 'err')
    return redirect(url_for('admin_panel'))

@app.route('/admin/gift_del/<int:gid>', methods=['POST'])
@admin_required
def admin_gift_del(gid):
    db = get_db()
    db.execute('DELETE FROM user_gifts WHERE gift_id=?', (gid,))
    db.execute('DELETE FROM gifts WHERE id=?', (gid,))
    db.commit()
    flash('Подарок удалён из каталога.', 'ok')
    return redirect(url_for('admin_panel'))

@app.route('/admin/gift_send', methods=['POST'])
@admin_required
def admin_gift_send():
    me = current_user()
    db = get_db()
    to_username = (request.form.get('to') or '').strip().lower()
    gift_id = request.form.get('gift_id', type=int)
    message = (request.form.get('message') or '').strip()[:140]
    anon = 1 if request.form.get('anon') else 0
    u = db.execute('SELECT * FROM users WHERE username=?', (to_username,)).fetchone()
    g = db.execute('SELECT * FROM gifts WHERE id=?', (gift_id,)).fetchone()
    if not u or not g:
        flash('Выберите получателя и подарок.', 'err')
        return redirect(url_for('admin_panel'))
    db.execute('INSERT INTO user_gifts(from_id, to_id, gift_id, message, is_anon, created_at) VALUES(?,?,?,?,?,?)',
               (me['id'], u['id'], gift_id, message, anon, now()))
    db.commit()
    who = '🎭 Аноним' if anon else me['name']
    notify(u['id'], 'gift', f"{who} подарил вам «{g['name']}» {g['emoji']}", url_for('profile', username=u['username']) + '?tab=gifts')
    flash(f"Подарок «{g['name']}» отправлен {u['name']}" + (' — анонимно 🎭' if anon else '') + '!', 'ok')
    return redirect(url_for('admin_panel'))

@app.route('/admin/user_ban/<int:uid>', methods=['POST'])
@admin_required
def admin_user_ban(uid):
    me = current_user()
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if u and u['id'] != me['id']:
        db.execute('UPDATE users SET is_banned=? WHERE id=?', (0 if u['is_banned'] else 1, uid))
        db.commit()
        flash((f"Бан снят с {u['name']}." if u['is_banned'] else f"{u['name']} забанен."), 'ok')
    return redirect(url_for('admin_panel'))

@app.route('/admin/user_admin/<int:uid>', methods=['POST'])
@admin_required
def admin_user_admin(uid):
    me = current_user()
    db = get_db()
    u = db.execute('SELECT * FROM users WHERE id=?', (uid,)).fetchone()
    if u and u['id'] != me['id']:
        db.execute('UPDATE users SET is_admin=? WHERE id=?', (0 if u['is_admin'] else 1, uid))
        db.commit()
        flash((f"Админка отозвана у {u['name']}." if u['is_admin'] else f"{u['name']} теперь тоже админ."), 'ok')
    return redirect(url_for('admin_panel'))

@app.route('/admin/del_q/<int:qid>', methods=['POST'])
@admin_required
def admin_del_q(qid):
    db = get_db()
    db.execute('DELETE FROM likes WHERE qid=?', (qid,))
    db.execute('DELETE FROM questions WHERE id=?', (qid,))
    db.commit()
    flash('Удалено.', 'ok')
    return redirect(url_for('admin_panel'))

# ---------- уведомления, поиск, настройки ----------
@app.route('/notifications')
@login_required
def notifications():
    me = current_user()
    db = get_db()
    rows = db.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50', (me['id'],)).fetchall()
    db.execute('UPDATE notifications SET is_read=1 WHERE user_id=?', (me['id'],))
    db.commit()
    return render_template('notifications.html', rows=rows)

@app.route('/search')
def search():
    q = (request.args.get('q') or '').strip()[:50]
    users = []
    if q:
        like = '%' + q + '%'
        users = get_db().execute('SELECT * FROM users WHERE username LIKE ? OR name LIKE ? ORDER BY name LIMIT 30', (like, like)).fetchall()
    return render_template('search.html', q=q, users=users)

@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    me = current_user()
    db = get_db()
    if request.method == 'POST':
        old = request.form.get('old_password') or ''
        new = request.form.get('new_password') or ''
        new2 = request.form.get('new_password2') or ''
        if not check_password_hash(me['password_hash'], old):
            flash('Старый пароль неверный.', 'err')
        elif len(new) < 4:
            flash('Новый пароль — минимум 4 символа.', 'err')
        elif new != new2:
            flash('Новые пароли не совпадают.', 'err')
        else:
            db.execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(new), me['id']))
            db.commit()
            flash('Пароль изменён.', 'ok')
            return redirect(url_for('settings'))
    return render_template('settings.html')

@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    me = current_user()
    db = get_db()
    if request.method == 'POST':
        name = (request.form.get('name') or '').strip()[:50] or me['name']
        bio = (request.form.get('bio') or '').strip()[:160]
        city = (request.form.get('city') or '').strip()[:50]
        avatar = me['avatar']
        f = request.files.get('avatar')
        if f and f.filename:
            ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
            if ext not in ALLOWED_EXT:
                flash('Фото: только PNG, JPG или GIF.', 'err')
                return render_template('edit_profile.html')
            fname = f"{me['username']}_{now()}.{ext}"
            f.save(os.path.join(AVATAR_DIR, fname))
            if avatar:
                try: os.remove(os.path.join(AVATAR_DIR, avatar))
                except OSError: pass
            avatar = fname
        db.execute('UPDATE users SET name=?, bio=?, city=?, avatar=? WHERE id=?', (name, bio, city, avatar, me['id']))
        db.commit()
        flash('Профиль сохранён.', 'ok')
        return redirect(url_for('profile', username=me['username']))
    return render_template('edit_profile.html')

@app.route('/avatars/<path:fn>')
def avatar_file(fn):
    return send_from_directory(AVATAR_DIR, fn)

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5000, debug=True)
