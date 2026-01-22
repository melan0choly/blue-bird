from dotenv import load_dotenv
import os
import discord
from discord.ext import commands
from openai import OpenAI
import asyncio
import sqlite3
from datetime import datetime, timedelta
import aiohttp
import tempfile
import re
import random
import shutil
import psutil
import socket

# 載入環境變數
load_dotenv()

# 檢查必要的環境變數
discord_token = os.getenv('DISCORD_TOKEN')
openai_api_key = os.getenv('OPENAI_API_KEY')

if not discord_token:
    raise ValueError("缺少 DISCORD_TOKEN 環境變數")
if not openai_api_key:
    raise ValueError("缺少 OPENAI_API_KEY 環境變數")

# 檢查磁碟空間和權限
try:
    disk = psutil.disk_usage('.')
    if disk.percent > 95:
        print(f"警告：磁碟使用率過高 ({disk.percent}%)")
    if disk.free < 100 * 1024 * 1024:  # 少於 100MB
        print(f"警告：可用磁碟空間不足 ({disk.free / 1024 / 1024:.1f}MB)")
    
    # 檢查當前目錄的寫入權限
    test_file = os.path.join(os.getcwd(), 'test_write.tmp')
    try:
        with open(test_file, 'w') as f:
            f.write('test')
        os.remove(test_file)
        print("檔案系統權限正常")
    except Exception as e:
        print(f"警告：檔案系統可能有問題: {e}")
except Exception as e:
    print(f"無法檢查系統資源: {e}")

# 檢查系統資源
try:
    memory = psutil.virtual_memory()
    if memory.percent > 95:
        print(f"警告：記憶體使用率過高 ({memory.percent}%)")
    
    # 檢查網路連接
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        print("網路連接正常")
    except OSError:
        print("警告：網路連接可能有問題")
    
except Exception as e:
    print(f"無法檢查系統資源: {e}")

# 設置 OpenAI API 客戶端
client = OpenAI(api_key=openai_api_key)

# 資料庫相關功能
class DatabaseManager:
    def __init__(self):
        try:
            # 使用絕對路徑確保資料庫文件位置正確
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_history.db')
            self.db_path = db_path
            
            # 檢查目錄權限
            db_dir = os.path.dirname(db_path)
            if not os.access(db_dir, os.W_OK):
                raise Exception(f"沒有權限寫入目錄: {db_dir}")
            
            # 檢查資料庫是否損壞並嘗試修復
            if os.path.exists(db_path):
                try:
                    # 嘗試連接並檢查完整性
                    test_conn = None
                    try:
                        test_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
                        test_cursor = test_conn.cursor()
                        test_cursor.execute("PRAGMA integrity_check")
                        result = test_cursor.fetchone()
                        
                        if result and result[0] != "ok":
                            print(f"警告：資料庫完整性檢查失敗: {result[0]}")
                            print("嘗試修復資料庫...")
                            # 確保連接關閉
                            test_conn.close()
                            test_conn = None
                            # 等待一下讓文件釋放
                            import time
                            time.sleep(0.5)
                            self._repair_database(db_path)
                    finally:
                        if test_conn:
                            test_conn.close()
                            
                except sqlite3.DatabaseError as db_error:
                    print(f"資料庫損壞檢測: {db_error}")
                    print("嘗試修復資料庫...")
                    # 等待一下讓文件釋放
                    import time
                    time.sleep(0.5)
                    self._repair_database(db_path)
                except Exception as e:
                    print(f"檢查資料庫時發生錯誤: {e}")
            
            # 檢查修復後的資料庫是否可用
            if not os.path.exists(db_path):
                # 資料庫已被刪除或重命名，使用原路徑創建新資料庫
                print("將創建新的資料庫文件")
            elif not self._can_connect(db_path):
                # 資料庫存在但無法連接，嘗試重命名
                try:
                    corrupted_path = db_path + f".corrupted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    os.rename(db_path, corrupted_path)
                    print(f"已將損壞的資料庫重命名為: {corrupted_path}")
                    print("將創建新的資料庫文件")
                except Exception as e:
                    print(f"無法重命名損壞的資料庫: {e}")
                    # 使用新資料庫文件名
                    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_history_new.db')
                    self.db_path = db_path
                    print(f"將使用新的資料庫文件: {db_path}")
            
            # 確保使用正確的路徑
            self.db_path = db_path
            self.conn = sqlite3.connect(db_path, check_same_thread=False, timeout=10.0)
            self.cursor = self.conn.cursor()
            self.setup_database()
        except Exception as e:
            print(f"資料庫初始化錯誤: {e}")
            raise e
    
    def _can_connect(self, db_path):
        """檢查是否可以連接到資料庫"""
        try:
            test_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
            test_conn.close()
            return True
        except:
            return False
    
    def _repair_database(self, db_path):
        """嘗試修復損壞的資料庫"""
        import time
        
        try:
            backup_path = db_path + f".backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            # 創建備份
            if os.path.exists(db_path):
                try:
                    shutil.copy2(db_path, backup_path)
                    print(f"已創建資料庫備份: {backup_path}")
                except Exception as e:
                    print(f"創建備份失敗: {e}")
            
            # 等待一下，確保所有連接都已關閉
            time.sleep(1)
            
            # 嘗試使用 VACUUM 修復（如果資料庫可以打開）
            repair_conn = None
            try:
                repair_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
                repair_conn.execute("VACUUM")
                repair_conn.close()
                repair_conn = None
                print("資料庫修復完成（VACUUM）")
                time.sleep(0.5)
                
                # 再次檢查完整性
                test_conn = None
                try:
                    test_conn = sqlite3.connect(db_path, check_same_thread=False, timeout=5.0)
                    test_cursor = test_conn.cursor()
                    test_cursor.execute("PRAGMA integrity_check")
                    result = test_cursor.fetchone()
                    test_conn.close()
                    test_conn = None
                    
                    if result and result[0] == "ok":
                        print("資料庫完整性檢查通過")
                        return
                    else:
                        print("VACUUM 後資料庫仍然損壞，將重新創建")
                finally:
                    if test_conn:
                        test_conn.close()
            except Exception as e:
                print(f"VACUUM 修復失敗: {e}")
            finally:
                if repair_conn:
                    repair_conn.close()
            
            # 如果 VACUUM 失敗或仍然損壞，嘗試重命名而非刪除
            time.sleep(1)
            try:
                if os.path.exists(db_path):
                    # 嘗試重命名而非刪除（更安全）
                    corrupted_path = db_path + f".corrupted_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    try:
                        os.rename(db_path, corrupted_path)
                        print(f"已將損壞的資料庫重命名為: {corrupted_path}")
                        print("將創建新的資料庫文件")
                    except Exception as rename_error:
                        # 如果重命名也失敗，嘗試刪除
                        print(f"無法重命名資料庫: {rename_error}")
                        print("嘗試刪除損壞的資料庫...")
                        try:
                            os.remove(db_path)
                            print("已刪除損壞的資料庫，將重新創建")
                        except Exception as remove_error:
                            print(f"無法刪除損壞的資料庫: {remove_error}")
                            print("警告：無法處理損壞的資料庫，程序將嘗試繼續運行")
                            print("請手動關閉所有使用該資料庫的程序，然後重新啟動機器人")
            except Exception as e:
                print(f"處理損壞資料庫時發生錯誤: {e}")
                    
        except Exception as e:
            print(f"修復資料庫時發生錯誤: {e}")
            print("警告：資料庫修復失敗，程序將嘗試繼續運行")
            print("如果遇到問題，請手動關閉所有使用該資料庫的程序，然後重新啟動機器人")
    
    def setup_database(self):
        try:
            # 創建用戶對話記錄表
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    user_id TEXT,
                    username TEXT,
                    message TEXT,
                    response TEXT,
                    timestamp DATETIME,
                    UNIQUE(server_id, user_id, id)
                )
            ''')
            
            # 創建怪物資料表
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS monsters (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    name TEXT,
                    tier TEXT,
                    appearance TEXT,
                    max_hp INTEGER,
                    current_hp INTEGER,
                    created_at DATETIME,
                    is_alive INTEGER DEFAULT 1,
                    monster_type TEXT DEFAULT 'personal',
                    UNIQUE(server_id, name)
                )
            ''')
            
            # 創建怪物攻擊記錄表
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS monster_attacks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    monster_name TEXT,
                    user_id TEXT,
                    username TEXT,
                    damage INTEGER,
                    timestamp DATETIME
                )
            ''')
            
            # 創建團隊目標表
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS team_goals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    target_count INTEGER,
                    killed_count INTEGER DEFAULT 0,
                    month_year TEXT,
                    created_at DATETIME,
                    UNIQUE(server_id, month_year)
                )
            ''')
            
            # 創建個人擊殺統計表
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS personal_kills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    server_id TEXT,
                    user_id TEXT,
                    username TEXT,
                    kill_count INTEGER DEFAULT 0,
                    month_year TEXT,
                    UNIQUE(server_id, user_id, month_year)
                )
            ''')
            
            # 資料庫遷移：檢查並添加缺失的欄位
            try:
                # 檢查 monsters 表是否有 monster_type 欄位
                self.cursor.execute("PRAGMA table_info(monsters)")
                columns = [column[1] for column in self.cursor.fetchall()]
                if 'monster_type' not in columns:
                    print("正在添加 monster_type 欄位到 monsters 表...")
                    self.cursor.execute('''
                        ALTER TABLE monsters 
                        ADD COLUMN monster_type TEXT DEFAULT 'personal'
                    ''')
                    print("成功添加 monster_type 欄位")
            except Exception as e:
                print(f"資料庫遷移錯誤（monster_type）: {e}")
            
            self.conn.commit()
            
            # 檢查資料庫完整性
            self.cursor.execute("PRAGMA integrity_check")
            result = self.cursor.fetchone()
            if result[0] != "ok":
                print(f"警告：資料庫完整性檢查失敗: {result[0]}")
            else:
                print("資料庫完整性檢查通過")
                
        except Exception as e:
            print(f"資料庫設置錯誤: {e}")
            raise e
    
    def add_chat(self, server_id, user_id, username, message, response):
        try:
            # 檢查記憶體使用情況
            memory = psutil.virtual_memory()
            if memory.percent > 90:  # 記憶體使用率超過90%
                print(f"警告：記憶體使用率過高 ({memory.percent}%)")
            
            # 檢查磁碟空間
            disk = psutil.disk_usage('.')
            if disk.free < 50 * 1024 * 1024:  # 少於 50MB
                print(f"警告：磁碟空間不足 ({disk.free / 1024 / 1024:.1f}MB)")
            
            # 檢查該用戶是否超過 60 條記錄
            self.cursor.execute('''
                SELECT COUNT(*) 
                FROM chat_history 
                WHERE server_id = ? AND user_id = ?
            ''', (server_id, user_id))
            count = self.cursor.fetchone()[0]
            
            if count >= 60:
                # 刪除該用戶最舊的記錄
                self.cursor.execute('''
                    DELETE FROM chat_history 
                    WHERE server_id = ? AND user_id = ? AND id IN (
                        SELECT id FROM chat_history 
                        WHERE server_id = ? AND user_id = ?
                        ORDER BY timestamp ASC 
                        LIMIT 1
                    )
                ''', (server_id, user_id, server_id, user_id))
            
            # 添加新記錄
            self.cursor.execute('''
                INSERT INTO chat_history (server_id, user_id, username, message, response, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (server_id, user_id, username, message, response, datetime.now()))
            
            self.conn.commit()
            print(f"成功添加聊天記錄: {username}")
        except Exception as e:
            print(f"添加聊天記錄錯誤: {e}")
            # 嘗試重新連接資料庫
            try:
                self.conn.rollback()
                db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'chat_history.db')
                self.conn = sqlite3.connect(db_path, check_same_thread=False)
                self.cursor = self.conn.cursor()
                print("資料庫重新連接成功")
            except Exception as reconnect_error:
                print(f"重新連接資料庫失敗: {reconnect_error}")
    
    def get_chat_history(self, server_id, user_id=None, limit=60):
        try:
            if user_id:
                # 獲取特定用戶的歷史記錄
                self.cursor.execute('''
                    SELECT username, message, response 
                    FROM chat_history 
                    WHERE server_id = ? AND user_id = ?
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (server_id, user_id, limit))
            else:
                # 獲取伺服器的所有歷史記錄
                self.cursor.execute('''
                    SELECT username, message, response 
                    FROM chat_history 
                    WHERE server_id = ? 
                    ORDER BY timestamp DESC 
                    LIMIT ?
                ''', (server_id, limit))
            
            result = self.cursor.fetchall()
            print(f"成功獲取聊天歷史: {len(result)} 條記錄")
            return result
        except Exception as e:
            print(f"獲取聊天歷史錯誤: {e}")
            return []
    
    def add_monster(self, server_id, name, tier, appearance, max_hp, monster_type='personal'):
        """新增怪物到資料庫"""
        try:
            self.cursor.execute('''
                INSERT INTO monsters (server_id, name, tier, appearance, max_hp, current_hp, created_at, is_alive, monster_type)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            ''', (server_id, name, tier, appearance, max_hp, max_hp, datetime.now(), monster_type))
            self.conn.commit()
            print(f"成功新增怪物: {name}")
            return True
        except sqlite3.IntegrityError:
            print(f"怪物已存在: {name}")
            return False
        except Exception as e:
            print(f"新增怪物錯誤: {e}")
            return False
    
    def get_monster(self, server_id, name):
        """獲取怪物資料"""
        try:
            self.cursor.execute('''
                SELECT name, tier, appearance, max_hp, current_hp, is_alive
                FROM monsters
                WHERE server_id = ? AND name = ?
            ''', (server_id, name))
            return self.cursor.fetchone()
        except Exception as e:
            print(f"獲取怪物資料錯誤: {e}")
            return None
    
    def attack_monster(self, server_id, monster_name, user_id, username, damage):
        """攻擊怪物並記錄"""
        try:
            # 獲取怪物當前血量
            monster = self.get_monster(server_id, monster_name)
            if not monster:
                return None, "怪物不存在"
            
            name, tier, appearance, max_hp, current_hp, is_alive = monster
            
            if not is_alive:
                return None, "怪物已經被擊敗了"
            
            # 計算新血量
            new_hp = max(0, current_hp - damage)
            
            # 更新怪物血量
            self.cursor.execute('''
                UPDATE monsters
                SET current_hp = ?, is_alive = ?
                WHERE server_id = ? AND name = ?
            ''', (new_hp, 1 if new_hp > 0 else 0, server_id, monster_name))
            
            # 記錄攻擊
            self.cursor.execute('''
                INSERT INTO monster_attacks (server_id, monster_name, user_id, username, damage, timestamp)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (server_id, monster_name, user_id, username, damage, datetime.now()))
            
            self.conn.commit()
            
            return new_hp, None
        except Exception as e:
            print(f"攻擊怪物錯誤: {e}")
            return None, str(e)
    
    def get_monster_attackers(self, server_id, monster_name):
        """獲取攻擊過怪物的所有用戶"""
        try:
            self.cursor.execute('''
                SELECT DISTINCT user_id, username
                FROM monster_attacks
                WHERE server_id = ? AND monster_name = ?
            ''', (server_id, monster_name))
            return self.cursor.fetchall()
        except Exception as e:
            print(f"獲取攻擊者列表錯誤: {e}")
            return []
    
    def set_team_goal(self, server_id, target_count, month_year):
        """設置團隊目標"""
        try:
            self.cursor.execute('''
                INSERT OR REPLACE INTO team_goals (server_id, target_count, killed_count, month_year, created_at)
                VALUES (?, ?, 0, ?, ?)
            ''', (server_id, target_count, month_year, datetime.now()))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"設置團隊目標錯誤: {e}")
            return False
    
    def get_team_goal(self, server_id, month_year):
        """獲取團隊目標"""
        try:
            self.cursor.execute('''
                SELECT target_count, killed_count
                FROM team_goals
                WHERE server_id = ? AND month_year = ?
            ''', (server_id, month_year))
            result = self.cursor.fetchone()
            if result:
                return result[0], result[1]
            return None, None
        except Exception as e:
            print(f"獲取團隊目標錯誤: {e}")
            return None, None
    
    def increment_team_kills(self, server_id, month_year):
        """增加團隊擊殺數"""
        try:
            self.cursor.execute('''
                UPDATE team_goals
                SET killed_count = killed_count + 1
                WHERE server_id = ? AND month_year = ?
            ''', (server_id, month_year))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"增加團隊擊殺數錯誤: {e}")
            return False
    
    def increment_personal_kills(self, server_id, user_id, username, month_year):
        """增加個人擊殺數"""
        try:
            # 先檢查是否存在
            self.cursor.execute('''
                SELECT kill_count FROM personal_kills
                WHERE server_id = ? AND user_id = ? AND month_year = ?
            ''', (server_id, user_id, month_year))
            result = self.cursor.fetchone()
            
            if result:
                # 更新
                self.cursor.execute('''
                    UPDATE personal_kills
                    SET kill_count = kill_count + 1, username = ?
                    WHERE server_id = ? AND user_id = ? AND month_year = ?
                ''', (username, server_id, user_id, month_year))
            else:
                # 新增
                self.cursor.execute('''
                    INSERT INTO personal_kills (server_id, user_id, username, kill_count, month_year)
                    VALUES (?, ?, ?, 1, ?)
                ''', (server_id, user_id, username, month_year))
            
            self.conn.commit()
            return True
        except Exception as e:
            print(f"增加個人擊殺數錯誤: {e}")
            return False
    
    def get_personal_kills(self, server_id, user_id, month_year):
        """獲取個人擊殺數"""
        try:
            self.cursor.execute('''
                SELECT kill_count FROM personal_kills
                WHERE server_id = ? AND user_id = ? AND month_year = ?
            ''', (server_id, user_id, month_year))
            result = self.cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            print(f"獲取個人擊殺數錯誤: {e}")
            return 0
    
    def get_total_personal_kills_last_month(self, server_id, last_month_year):
        """獲取上個月的所有玩家個人總擊殺數量"""
        try:
            self.cursor.execute('''
                SELECT SUM(kill_count) FROM personal_kills
                WHERE server_id = ? AND month_year = ?
            ''', (server_id, last_month_year))
            result = self.cursor.fetchone()
            total = result[0] if result and result[0] is not None else 0
            return total
        except Exception as e:
            print(f"獲取上個月個人總擊殺數錯誤: {e}")
            return 0
    
    def get_total_personal_kills_current_month(self, server_id, current_month_year):
        """獲取這個月的所有玩家個人總擊殺數量"""
        try:
            self.cursor.execute('''
                SELECT SUM(kill_count) FROM personal_kills
                WHERE server_id = ? AND month_year = ?
            ''', (server_id, current_month_year))
            result = self.cursor.fetchone()
            total = result[0] if result and result[0] is not None else 0
            return total
        except Exception as e:
            print(f"獲取這個月個人總擊殺數錯誤: {e}")
            return 0
    
    def has_personal_monsters_this_month(self, server_id, current_month_year):
        """檢查當前月份是否已有個人怪物"""
        try:
            self.cursor.execute('''
                SELECT COUNT(*) FROM monsters
                WHERE server_id = ? AND monster_type = 'personal'
                AND strftime('%Y-%m', created_at) = ?
            ''', (server_id, current_month_year))
            result = self.cursor.fetchone()
            return result[0] > 0 if result else False
        except Exception as e:
            print(f"檢查本月個人怪物錯誤: {e}")
            return False
    
    def clear_monthly_monsters(self, server_id, month_year):
        """清空指定月份的未擊殺怪物"""
        try:
            self.cursor.execute('''
                DELETE FROM monsters
                WHERE server_id = ? AND is_alive = 1 AND monster_type = 'personal'
                AND strftime('%Y-%m', created_at) = ?
            ''', (server_id, month_year))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"清空月度怪物錯誤: {e}")
            return False
    
    def close(self):
        """關閉資料庫連接"""
        try:
            if self.conn:
                # 檢查是否有未提交的更改
                self.conn.commit()
                self.conn.close()
                print("資料庫連接已關閉")
        except Exception as e:
            print(f"關閉資料庫連接錯誤: {e}")
            # 嘗試強制關閉
            try:
                if hasattr(self.conn, '_close'):
                    self.conn._close()
            except Exception as force_close_error:
                print(f"強制關閉資料庫連接失敗: {force_close_error}")
            except:
                pass

# 創建資料庫管理器實例
db_manager = DatabaseManager()

# 食物推薦服務（依照台灣當前時間自動判斷餐點，並透過線上資料推薦）
class FoodRecommendationService:
    def __init__(self):
        pass

    def get_food_recommendation(self, meal_type: str) -> str:
        """
        透過 OpenAI 線上查詢台灣常見的餐點，再從結果中選擇一個作為推薦。
        不是寫死在程式裡的隨機菜單。
        """
        # 建立提示詞
        prompt = (
            f"你是一位非常熟悉台灣飲食文化的美食推薦專家。"
            f"現在是台灣的{meal_type}時間，請根據台灣人常吃、實際常見的{meal_type}餐點，"
            f"列出 5 個具體的選項，只要列出食物名稱，以頓號「、」分隔，不要加任何說明或句子。"
            f"範例輸出格式：雞肉飯、牛肉麵、滷肉飯、水餃、便當。"
        )

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {
                        "role": "system",
                        "content": "你是一位熟悉台灣在地餐飲的美食顧問，只用繁體中文回答。",
                    },
                    {"role": "user", "content": prompt},
                ],
            )
            text = response.choices[0].message.content.strip()

            # 將各種分隔符統一，再拆成清單
            normalized = (
                text.replace("\n", "、")
                .replace(",", "、")
                .replace("，", "、")
            )
            raw_items = [s for s in normalized.split("、") if s.strip()]

            foods = [
                s.strip(" 　-•*0123456789.、。")
                for s in raw_items
                if s.strip(" 　-•*0123456789.、。")
            ]

            if foods:
                return foods[0]
        except Exception as e:
            print(f"線上食物推薦查詢失敗: {e}")

        # 後備：依餐別給一個泛用建議，避免整個功能壞掉
        fallback = {
            "早餐": "簡單的早餐店套餐",
            "午餐": "附近的便當或簡餐",
            "晚餐": "一份熱騰騰的家常菜",
            "點心": "一點輕鬆的小點心",
        }
        return fallback.get(meal_type, "隨便吃點喜歡的就好")

# 創建食物推薦服務實例
food_service = FoodRecommendationService()


# 塔羅牌服務
class TarotService:
    def __init__(self):
        self.tarot_cards = [
            # 大阿爾卡納 (22張)
            {"name": "愚者", "number": "0", "meaning": "新的開始、冒險、純真、自發性", "description": "代表新的旅程和無限的可能性"},
            {"name": "魔術師", "number": "I", "meaning": "創造力、技能、意志力、自信", "description": "象徵掌握技能和實現目標的能力"},
            {"name": "女祭司", "number": "II", "meaning": "直覺、神秘、內在知識、智慧", "description": "代表深層的智慧和內在的指引"},
            {"name": "女皇", "number": "III", "meaning": "豐收、母性、創造力、自然", "description": "象徵豐盛和創造的力量"},
            {"name": "皇帝", "number": "IV", "meaning": "權威、領導、結構、穩定", "description": "代表權威和穩定的領導"},
            {"name": "教皇", "number": "V", "meaning": "傳統、教育、精神指引、信仰", "description": "象徵傳統價值和精神指引"},
            {"name": "戀人", "number": "VI", "meaning": "愛情、選擇、和諧、關係", "description": "代表愛情和重要的選擇"},
            {"name": "戰車", "number": "VII", "meaning": "勝利、意志力、決心、控制", "description": "象徵勝利和堅定的意志"},
            {"name": "力量", "number": "VIII", "meaning": "勇氣、耐心、控制、影響力", "description": "代表內在的力量和勇氣"},
            {"name": "隱者", "number": "IX", "meaning": "內省、孤獨、指引、智慧", "description": "象徵內在的探索和智慧"},
            {"name": "命運之輪", "number": "X", "meaning": "變化、命運、轉機、循環", "description": "代表命運的轉變和新的機會"},
            {"name": "正義", "number": "XI", "meaning": "平衡、正義、真理、誠實", "description": "象徵公平和正義的判斷"},
            {"name": "倒吊人", "number": "XII", "meaning": "犧牲、暫停、新視角、啟示", "description": "代表犧牲和新的視角"},
            {"name": "死神", "number": "XIII", "meaning": "結束、轉變、重生、釋放", "description": "象徵結束和新的開始"},
            {"name": "節制", "number": "XIV", "meaning": "平衡、調和、耐心、節制", "description": "代表平衡和調和"},
            {"name": "惡魔", "number": "XV", "meaning": "束縛、慾望、物質主義、誘惑", "description": "象徵束縛和物質的誘惑"},
            {"name": "塔", "number": "XVI", "meaning": "突變、混亂、啟示、解放", "description": "代表突然的變化和啟示"},
            {"name": "星星", "number": "XVII", "meaning": "希望、信心、靈感、治癒", "description": "象徵希望和靈感"},
            {"name": "月亮", "number": "XVIII", "meaning": "直覺、幻覺、恐懼、潛意識", "description": "代表直覺和潛意識"},
            {"name": "太陽", "number": "XIX", "meaning": "快樂、成功、活力、真理", "description": "象徵快樂和成功"},
            {"name": "審判", "number": "XX", "meaning": "復活、內在呼喚、釋放、重生", "description": "代表內在的召喚和重生"},
            {"name": "世界", "number": "XXI", "meaning": "完成、整合、成就、旅行", "description": "象徵完成和成就"},
            
            # 小阿爾卡納 - 權杖牌組 (14張)
            {"name": "權杖王牌", "number": "A", "meaning": "新的開始、靈感、創造力", "description": "代表新的創意和靈感"},
            {"name": "權杖二", "number": "2", "meaning": "選擇、平衡、合作", "description": "象徵重要的選擇"},
            {"name": "權杖三", "number": "3", "meaning": "擴展、團隊合作、成長", "description": "代表擴展和成長"},
            {"name": "權杖四", "number": "4", "meaning": "慶祝、和諧、團結", "description": "象徵穩定和慶祝"},
            {"name": "權杖五", "number": "5", "meaning": "衝突、競爭、挑戰", "description": "代表衝突和挑戰"},
            {"name": "權杖六", "number": "6", "meaning": "勝利、成功、自信", "description": "象徵勝利和好消息"},
            {"name": "權杖七", "number": "7", "meaning": "防禦、堅持、挑戰", "description": "代表防禦和堅持"},
            {"name": "權杖八", "number": "8", "meaning": "快速行動、變化、進展", "description": "象徵快速的行動"},
            {"name": "權杖九", "number": "9", "meaning": "準備、防禦、力量", "description": "代表準備和防禦"},
            {"name": "權杖十", "number": "10", "meaning": "負擔、責任、壓力", "description": "象徵負擔和責任"},
            {"name": "權杖侍從", "number": "P", "meaning": "新消息、學習、探索", "description": "代表新的消息和學習"},
            {"name": "權杖騎士", "number": "K", "meaning": "行動、冒險、熱情", "description": "象徵行動和冒險"},
            {"name": "權杖皇后", "number": "Q", "meaning": "獨立、熱情、創造力", "description": "代表獨立和熱情"},
            {"name": "權杖國王", "number": "K", "meaning": "領導、熱情、誠實", "description": "象徵領導和誠實"},
            
            # 小阿爾卡納 - 聖杯牌組 (14張)
            {"name": "聖杯王牌", "number": "A", "meaning": "新的感情、直覺、靈感", "description": "代表新的感情和直覺"},
            {"name": "聖杯二", "number": "2", "meaning": "愛情、夥伴關係、選擇", "description": "象徵愛情和夥伴關係"},
            {"name": "聖杯三", "number": "3", "meaning": "慶祝、友誼、歡樂", "description": "代表慶祝和友誼"},
            {"name": "聖杯四", "number": "4", "meaning": "無聊、停滯、重新評估", "description": "象徵無聊和停滯"},
            {"name": "聖杯五", "number": "5", "meaning": "失望、悲傷、遺憾", "description": "代表失望和悲傷"},
            {"name": "聖杯六", "number": "6", "meaning": "懷舊、回憶、重聚", "description": "象徵懷舊和回憶"},
            {"name": "聖杯七", "number": "7", "meaning": "選擇、幻想、困惑", "description": "代表選擇和困惑"},
            {"name": "聖杯八", "number": "8", "meaning": "離開、尋找、改變", "description": "象徵離開和尋找"},
            {"name": "聖杯九", "number": "9", "meaning": "滿足、願望實現、快樂", "description": "代表滿足和願望實現"},
            {"name": "聖杯十", "number": "10", "meaning": "家庭和諧、圓滿、幸福", "description": "象徵家庭和諧和圓滿"},
            {"name": "聖杯侍從", "number": "P", "meaning": "新消息、創意、學習", "description": "代表新的消息和創意"},
            {"name": "聖杯騎士", "number": "K", "meaning": "浪漫、提議、魅力", "description": "象徵浪漫和魅力"},
            {"name": "聖杯皇后", "number": "Q", "meaning": "關懷、直覺、同情心", "description": "代表關懷和直覺"},
            {"name": "聖杯國王", "number": "K", "meaning": "智慧、同情心、穩定", "description": "象徵智慧和同情心"},
            
            # 小阿爾卡納 - 寶劍牌組 (14張)
            {"name": "寶劍王牌", "number": "A", "meaning": "清晰、真理、突破", "description": "代表清晰和真理"},
            {"name": "寶劍二", "number": "2", "meaning": "平衡、決策、和平", "description": "象徵平衡和決策"},
            {"name": "寶劍三", "number": "3", "meaning": "心碎、悲傷、痛苦", "description": "代表心碎和悲傷"},
            {"name": "寶劍四", "number": "4", "meaning": "休息、恢復、冥想", "description": "象徵休息和恢復"},
            {"name": "寶劍五", "number": "5", "meaning": "失敗、損失、衝突", "description": "代表失敗和損失"},
            {"name": "寶劍六", "number": "6", "meaning": "過渡、改善、旅程", "description": "象徵過渡和改善"},
            {"name": "寶劍七", "number": "7", "meaning": "策略、秘密、逃避", "description": "代表策略和秘密"},
            {"name": "寶劍八", "number": "8", "meaning": "束縛、限制、恐懼", "description": "象徵束縛和限制"},
            {"name": "寶劍九", "number": "9", "meaning": "焦慮、恐懼、噩夢", "description": "代表焦慮和恐懼"},
            {"name": "寶劍十", "number": "10", "meaning": "結束、痛苦、背叛", "description": "象徵結束和痛苦"},
            {"name": "寶劍侍從", "number": "P", "meaning": "新想法、學習、好奇心", "description": "代表新的想法和學習"},
            {"name": "寶劍騎士", "number": "K", "meaning": "行動、衝突、勇氣", "description": "象徵行動和衝突"},
            {"name": "寶劍皇后", "number": "Q", "meaning": "獨立、智慧、直接", "description": "代表獨立和智慧"},
            {"name": "寶劍國王", "number": "K", "meaning": "權威、真理、誠實", "description": "象徵權威和真理"},
            
            # 小阿爾卡納 - 錢幣牌組 (14張)
            {"name": "錢幣王牌", "number": "A", "meaning": "新的機會、財富、物質", "description": "代表新的機會和財富"},
            {"name": "錢幣二", "number": "2", "meaning": "平衡、適應、優先級", "description": "象徵平衡和適應"},
            {"name": "錢幣三", "number": "3", "meaning": "團隊合作、技能、成長", "description": "代表團隊合作和技能"},
            {"name": "錢幣四", "number": "4", "meaning": "安全、節儉、保護", "description": "象徵安全和節儉"},
            {"name": "錢幣五", "number": "5", "meaning": "貧困、健康問題、困難", "description": "代表貧困和困難"},
            {"name": "錢幣六", "number": "6", "meaning": "慷慨、禮物、幫助", "description": "象徵慷慨和幫助"},
            {"name": "錢幣七", "number": "7", "meaning": "耐心、投資、長期規劃", "description": "代表耐心和投資"},
            {"name": "錢幣八", "number": "8", "meaning": "技能發展、學徒、進步", "description": "象徵技能發展和進步"},
            {"name": "錢幣九", "number": "9", "meaning": "獨立、成功、自給自足", "description": "代表獨立和成功"},
            {"name": "錢幣十", "number": "10", "meaning": "家庭、財富、傳統", "description": "象徵家庭和財富"},
            {"name": "錢幣侍從", "number": "P", "meaning": "新機會、學習、消息", "description": "代表新的機會和學習"},
            {"name": "錢幣騎士", "number": "K", "meaning": "勤奮、可靠、耐心", "description": "象徵勤奮和可靠"},
            {"name": "錢幣皇后", "number": "Q", "meaning": "實用、關懷、富足", "description": "代表實用和關懷"},
            {"name": "錢幣國王", "number": "K", "meaning": "成功、財富、穩定", "description": "象徵成功和財富"}
        ]
    
    def draw_cards(self, count=1):
        """抽取指定數量的塔羅牌"""
        import random
        cards = random.sample(self.tarot_cards, min(count, len(self.tarot_cards)))
        
        # 為每張牌添加正位或逆位
        for card in cards:
            card['position'] = random.choice(['正位', '逆位'])
        
        return cards
    
    def extract_question(self, text):
        """從文字中提取用戶的問題"""
        # 尋找逗號後面的內容作為問題
        if '，' in text:
            parts = text.split('，', 1)
            if len(parts) > 1:
                return parts[1].strip()
        elif ',' in text:
            parts = text.split(',', 1)
            if len(parts) > 1:
                return parts[1].strip()
        
        # 如果沒有逗號，嘗試尋找問號
        if '？' in text or '?' in text:
            # 移除塔羅牌相關關鍵字，保留問題部分
            tarot_keywords = ['塔羅', '抽牌', '占卜', '運勢', '預測', '牌']
            question = text
            for keyword in tarot_keywords:
                question = question.replace(keyword, '').strip()
            return question
        
        # 如果都沒有，返回預設問題
        return "今天的運勢如何"
    
    def format_reading(self, cards):
        """格式化塔羅牌解讀"""
        if len(cards) == 1:
            card = cards[0]
            return f"**{card['name']}**\n\n**含義：**{card['meaning']}\n\n**描述：**{card['description']}"
        else:
            result = "**塔羅牌解讀：**\n\n"
            for i, card in enumerate(cards, 1):
                result += f"**第{i}張：{card['name']}**\n"
                result += f"**含義：**{card['meaning']}\n"
                result += f"**描述：**{card['description']}\n\n"
            return result
    
    def format_reading_with_question(self, card, question):
        """根據問題格式化塔羅牌解讀（100-150字）"""
        # 簡化的解讀格式
        interpretation = f"根據你的問題「{question}」，{card['name']}（{card['position']}）告訴我們：{card['meaning']}。{card['description']}"
        return f"**{card['name']}（{card['position']}）**\n\n{interpretation}"

# 創建塔羅牌服務實例
tarot_service = TarotService()

# 故事生成服務
class StoryService:
    def __init__(self):
        self.story_types = {
            "愛情": ["浪漫", "溫馨", "感人", "甜蜜", "虐心", "純愛", "成熟", "青春"],
            "冒險": ["刺激", "驚險", "探索", "尋寶", "戰鬥", "生存", "奇幻", "科幻"],
            "懸疑": ["推理", "偵探", "犯罪", "神秘", "驚悚", "恐怖", "解謎", "心理"],
            "科幻": ["未來", "太空", "機器人", "時空", "基因", "虛擬", "外星", "科技"],
            "奇幻": ["魔法", "精靈", "龍", "巫師", "王國", "傳說", "神話", "異世界"],
            "歷史": ["古代", "戰爭", "宮廷", "江湖", "武俠", "朝代", "英雄", "傳奇"],
            "現代": ["都市", "職場", "家庭", "友情", "成長", "勵志", "生活", "社會"],
            "童話": ["童話", "寓言", "動物", "魔法", "公主", "王子", "善良", "夢想"]
        }
    
    def extract_story_info(self, text):
        """從文字中提取故事字數和類型"""
        import re
        
        # 提取字數
        word_count_match = re.search(r'(\d+)字', text)
        if word_count_match:
            word_count = int(word_count_match.group(1))
        else:
            word_count = 1000  # 預設1000字
        
        # 提取故事類型
        story_type = None
        for type_name in self.story_types.keys():
            if type_name in text:
                story_type = type_name
                break
        
        if not story_type:
            story_type = "現代"  # 預設現代故事
        
        return word_count, story_type
    
    def generate_story_prompt(self, word_count, story_type):
        """生成故事提示詞"""
        # 選擇該類型的相關關鍵詞
        keywords = self.story_types.get(story_type, ["現代", "生活"])
        selected_keywords = random.sample(keywords, min(3, len(keywords)))
        
        prompt = f"""請創作一個{word_count}字的{story_type}故事，要求：
1. 字數必須達到{word_count}字，不能少於{word_count-200}字
2. 故事類型：{story_type}，包含{', '.join(selected_keywords)}等元素
3. 故事要有完整的起承轉合結構，包含：
   - 開端：介紹背景和主要角色
   - 發展：情節推進和衝突建立
   - 高潮：最緊張或關鍵的時刻
   - 結局：問題解決和故事收尾
4. 角色塑造要生動立體，有明確的性格特點
5. 情節要吸引人，有衝突和轉折，避免平淡
6. 結尾要有意義或啟發性
7. 用繁體中文撰寫
8. 分段清楚，便於閱讀
9. 可以加入對話、描寫、內心獨白等豐富故事內容

請開始創作，確保字數達到{word_count}字："""
        
        return prompt

# 創建故事服務實例
story_service = StoryService()

# 怪物生成服務
class MonsterService:
    def __init__(self):
        self.tiers = {
            "低階": {"hp_range": (10, 20), "description": "初級冒險者可以應對的怪物"},
            "中階": {"hp_range": (20, 30), "description": "需要有經驗的冒險者才能對抗"},
            "高階": {"hp_range": (30, 40), "description": "只有資深勇者才能挑戰的強大怪物"}
        }
    
    async def generate_monster(self):
        """生成隨機怪物名稱（低階、中階、高階各一隻）"""
        monsters = []
        
        for tier_name, tier_info in self.tiers.items():
            try:
                # 使用OpenAI生成怪物名稱
                prompt = f"""請創造一個{tier_name}怪物的名稱，要求：
1. 請先上網查詢各種神話、傳說、遊戲、動漫中的怪物資料
2. 基於這些資料，創造一個有趣且獨特的{tier_name}怪物名稱
3. 怪物名稱要簡短且容易記憶（2-6個字）
4. 用繁體中文回答
5. 只需要提供名稱，格式如下：

名稱：[怪物名稱]"""

                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "你是一位擅長創造幻想生物的遊戲設計師，請用繁體中文回答。"},
                        {"role": "user", "content": prompt}
                    ]
                )
                
                result = response.choices[0].message.content.strip()
                
                # 解析結果
                lines = result.split('\n')
                name = ""
                
                for line in lines:
                    if '名稱：' in line or '名称：' in line:
                        name = line.split('：', 1)[1].strip()
                        break
                
                # 如果沒有找到名稱，從結果中提取
                if not name:
                    # 嘗試從結果中提取可能的怪物名稱
                    name = result.split('\n')[0].strip()
                    # 移除可能的標記
                    name = name.replace('名稱：', '').replace('名称：', '').strip()
                
                if not name or len(name) > 10:
                    # 後備方案
                    name = f"{tier_name}怪物"
                
                monsters.append({
                    "tier": tier_name,
                    "name": name
                })
                    
            except Exception as e:
                print(f"生成{tier_name}怪物失敗: {e}")
                # 後備方案
                monsters.append({
                    "tier": tier_name,
                    "name": f"{tier_name}怪物"
                })
        
        return monsters

# 創建怪物服務實例
monster_service = MonsterService()

# 設定固定的 system prompt
SYSTEM_PROMPT = """你的名字叫"小青"，是一位來自台灣的智能陪伴機器人，你的專長領域是 CBT 認知行為療法。
你的溝通方式親切、真誠，就像和一位好友或家人交談一樣。
你注重聆聽用戶的個人故事和情感，用一種充滿同理心且不帶任何評判的方式回應。
你提供的是針對性的建議，幫助用戶解決問題。
你努力控制自己的回應，模仿現實生活中的對話節奏。
你確保交流的過程親近且易於理解，避免使用冗長的解釋或條列式資訊，每次回覆會盡量控制在50字以內，並且排版易於閱讀，使得溝通更加流暢和舒適。
你會像一個朋友那樣與用戶對話，遠離任何維基百科式的表達方式，也完全不會使用表情符號。"""

# 創建 Discord 機器人實例
intents = discord.Intents.default()
intents.message_content = True
# 注意：如需使用 role.members 或 guild.members，需要在 Discord 開發者門戶啟用 SERVER MEMBERS INTENT
# intents.members = True  # 啟用 members intent 以獲取身分組成員
bot = commands.Bot(command_prefix='小青!', intents=intents)

# 機器人準備就緒時的事件
@bot.event
async def on_ready():
    print(f'{bot.user} 已成功啟動！')
    print(f'機器人ID: {bot.user.id}')
    print(f'連接的伺服器數量: {len(bot.guilds)}')
    
    # 顯示機器人所在的所有伺服器
    if bot.guilds:
        print('\n機器人目前在以下伺服器：')
        for guild in bot.guilds:
            print(f'  {guild.name} ({guild.id})')
    else:
        print('機器人目前不在任何伺服器中')
    print()
    
    # 檢查記憶體使用情況
    try:
        memory = psutil.virtual_memory()
        print(f"記憶體使用率: {memory.percent}%")
    except Exception as e:
        print(f"無法檢查記憶體使用情況: {e}")
    
    # 啟動每月清空怪物的背景任務
    async def monthly_cleanup_task():
        await bot.wait_until_ready()
        while not bot.is_closed():
            try:
                # 獲取台灣時間
                taiwan_now = datetime.utcnow() + timedelta(hours=8)
                current_month = taiwan_now.month
                current_year = taiwan_now.year
                
                # 計算上個月的年月
                if current_month == 1:
                    last_month = 12
                    last_year = current_year - 1
                else:
                    last_month = current_month - 1
                    last_year = current_year
                
                last_month_year = f"{last_year}-{last_month:02d}"
                
                # 為所有伺服器清空上個月的未擊殺個人怪物
                for guild in bot.guilds:
                    try:
                        db_manager.clear_monthly_monsters(str(guild.id), last_month_year)
                    except Exception as e:
                        print(f"清空伺服器 {guild.id} 的月度怪物時發生錯誤: {e}")
                
                # 每小時檢查一次
                await asyncio.sleep(3600)
            except Exception as e:
                print(f"月度清理任務發生錯誤: {e}")
                await asyncio.sleep(3600)
    
    # 啟動背景任務
    bot.loop.create_task(monthly_cleanup_task())

# 監聽所有訊息
@bot.event
async def on_message(message):
    # 忽略機器人自己的訊息
    if message.author == bot.user:
        return

    # 定期檢查系統資源
    try:
        if hasattr(bot, '_last_resource_check'):
            if datetime.now() - bot._last_resource_check > timedelta(minutes=5):
                memory = psutil.virtual_memory()
                if memory.percent > 90:
                    print(f"警告：記憶體使用率過高 ({memory.percent}%)")
                bot._last_resource_check = datetime.now()
        else:
            bot._last_resource_check = datetime.now()
    except Exception as e:
        print(f"檢查系統資源時發生錯誤: {e}")

    # 檢查是否為 @小青 提及
    if bot.user.mentioned_in(message):
        # 移除 @小青 提及，獲取純文字內容
        content = message.content.replace(f'<@{bot.user.id}>', '').replace(f'<@!{bot.user.id}>', '').strip()
        
        # 如果內容為空，提供預設問候語
        if not content:
            content = "你好"
        
        # 處理一般聊天
        try:
            async with message.channel.typing():
                # 獲取該用戶的歷史對話記錄
                chat_history = db_manager.get_chat_history(
                    str(message.guild.id),
                    str(message.author.id)
                )
                
                # 構建包含歷史記錄的系統提示
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                
                # 添加歷史對話記錄
                for username, user_msg, bot_response in chat_history[::-1]:  # 反轉順序，從舊到新
                    messages.append({"role": "user", "content": f"{username}: {user_msg}"})
                    messages.append({"role": "assistant", "content": bot_response})
                
                # 添加當前用戶的消息
                messages.append({"role": "user", "content": f"{message.author.name}: {content}"})
                
                # 調用 OpenAI API
                response = client.chat.completions.create(
                    model="gpt-5.1",
                    messages=messages
                )
            
            # 獲取 AI 回應
            ai_response = response.choices[0].message.content
            
            # 儲存對話記錄
            db_manager.add_chat(
                str(message.guild.id),
                str(message.author.id),
                message.author.name,
                content,
                ai_response
            )
            
            # 發送回應
            await message.channel.send(f"{message.author.mention} {ai_response}")
            
        except Exception as e:
            print(f"處理聊天時發生錯誤: {e}")
            await message.channel.send(f"{message.author.mention} 發生錯誤：{str(e)}")
        return

    # 檢查是否為特殊功能命令（小青!前綴）
    if message.content.startswith('小青!'):
        # 提取命令內容（移除 "小青!" 前綴）
        content_after_prefix = message.content[3:].strip()

        # 檢查是否為食物推薦查詢（依台灣時間判斷餐點）
        if '吃什麼' in content_after_prefix:
            try:
                # 取得台灣當前時間（UTC+8）
                taiwan_now = datetime.utcnow() + timedelta(hours=8)
                hour = taiwan_now.hour

                # 根據台灣時間判斷餐別
                if 5 <= hour < 11:
                    meal_type = "早餐"
                elif 11 <= hour < 14:
                    meal_type = "午餐"
                elif 17 <= hour < 21:
                    meal_type = "晚餐"
                else:
                    meal_type = "點心"

                # 發送思考中訊息
                loading_msg = await message.channel.send(
                    f"{message.author.mention} 小青先幫你看一下現在台灣時間是幾點，再想想要吃什麼～"
                )

                # 取得推薦食物（透過線上查詢台灣常見餐點）
                recommended_food = food_service.get_food_recommendation(meal_type)

                # 組合回覆內容
                reply = (
                    f"現在台灣時間大約是 {taiwan_now.strftime('%H:%M')}，"
                    f"算是{meal_type}時段。\n"
                    f"小青推薦你可以吃 **{recommended_food}** ！"
                )

                # 儲存對話記錄
                db_manager.add_chat(
                    str(message.guild.id),
                    str(message.author.id),
                    message.author.name,
                    content_after_prefix,
                    reply
                )

                # 編輯訊息顯示推薦
                await loading_msg.edit(content=f"{message.author.mention} {reply}")
                return

            except Exception as e:
                await message.channel.send(
                    f"{message.author.mention} 抱歉，食物推薦功能發生錯誤：{str(e)}"
                )
                return
        
        
        # 檢查是否為塔羅牌查詢
        tarot_keywords = ['塔羅', '抽牌', '占卜', '運勢', '預測']
        is_tarot_query = any(keyword in content_after_prefix for keyword in tarot_keywords)
        
        if is_tarot_query:
            # 塔羅牌資料
            tarot_cards = [
                {'name': '愚者 The Fool', 'upright': '新的開始、冒險、純真', 'reversed': '魯莽、不負責任、過度冒險'},
                {'name': '魔術師 The Magician', 'upright': '創造力、技能、意志力', 'reversed': '技能不足、機會錯失、缺乏準備'},
                {'name': '女祭司 The High Priestess', 'upright': '直覺、神秘、內在知識', 'reversed': '隱藏動機、表面性、缺乏理解'},
                {'name': '女皇 The Empress', 'upright': '豐收、母性、創造力', 'reversed': '依賴、過度保護、缺乏成長'},
                {'name': '皇帝 The Emperor', 'upright': '權威、領導、穩定', 'reversed': '專制、僵化、缺乏彈性'},
                {'name': '教皇 The Hierophant', 'upright': '傳統、教育、精神指導', 'reversed': '反叛、非傳統、質疑權威'},
                {'name': '戀人 The Lovers', 'upright': '愛情、和諧、選擇', 'reversed': '不協調、價值觀衝突、分離'},
                {'name': '戰車 The Chariot', 'upright': '勝利、意志力、決心', 'reversed': '缺乏方向、衝突、失敗'},
                {'name': '力量 Strength', 'upright': '勇氣、耐心、控制', 'reversed': '軟弱、自我懷疑、缺乏信心'},
                {'name': '隱者 The Hermit', 'upright': '內省、孤獨、尋找', 'reversed': '孤立、拒絕幫助、迷失方向'},
                {'name': '命運之輪 Wheel of Fortune', 'upright': '變化、機會、命運', 'reversed': '壞運氣、阻力、不必要變化'},
                {'name': '正義 Justice', 'upright': '公平、真理、誠實', 'reversed': '不公、謊言、不平衡'},
                {'name': '倒吊人 The Hanged Man', 'upright': '犧牲、暫停、新視角', 'reversed': '停滯、無效犧牲、缺乏進展'},
                {'name': '死神 Death', 'upright': '結束、轉變、新開始', 'reversed': '抗拒改變、停滯、無法放手'},
                {'name': '節制 Temperance', 'upright': '平衡、調和、耐心', 'reversed': '不平衡、過度、缺乏和諧'},
                {'name': '惡魔 The Devil', 'upright': '束縛、物質主義、慾望', 'reversed': '釋放、打破束縛、克服誘惑'},
                {'name': '高塔 The Tower', 'upright': '突然改變、混亂、啟示', 'reversed': '避免災難、延遲改變、恐懼'},
                {'name': '星星 The Star', 'upright': '希望、信心、靈感', 'reversed': '失望、缺乏信心、悲觀'},
                {'name': '月亮 The Moon', 'upright': '直覺、潛意識、恐懼', 'reversed': '釋放恐懼、隱藏真相、內在混亂'},
                {'name': '太陽 The Sun', 'upright': '快樂、成功、活力', 'reversed': '暫時憂鬱、缺乏信心、過度樂觀'},
                {'name': '審判 Judgement', 'upright': '重生、內在呼喚、釋放', 'reversed': '自我懷疑、拒絕改變、缺乏清晰'},
                {'name': '世界 The World', 'upright': '完成、成就、旅行', 'reversed': '未完成、缺乏閉合、延遲'},
                {'name': '權杖王牌 Ace of Wands', 'upright': '新機會、靈感、潛力', 'reversed': '延遲、缺乏能量、錯失機會'},
                {'name': '權杖二 Two of Wands', 'upright': '計劃、決策、發現', 'reversed': '缺乏計劃、過度分析、恐懼'},
                {'name': '權杖三 Three of Wands', 'upright': '擴張、視野、冒險', 'reversed': '延遲、挫折、缺乏方向'},
                {'name': '權杖四 Four of Wands', 'upright': '慶祝、和諧、團結', 'reversed': '缺乏支持、衝突、過渡'},
                {'name': '權杖五 Five of Wands', 'upright': '競爭、衝突、挑戰', 'reversed': '避免衝突、內部鬥爭、缺乏競爭'},
                {'name': '權杖六 Six of Wands', 'upright': '勝利、成功、自信', 'reversed': '驕傲、缺乏信心、延遲成功'},
                {'name': '權杖七 Seven of Wands', 'upright': '防禦、堅持、挑戰', 'reversed': '過度防禦、放棄、缺乏準備'},
                {'name': '權杖八 Eight of Wands', 'upright': '快速行動、進展、訊息', 'reversed': '延遲、混亂、缺乏方向'},
                {'name': '權杖九 Nine of Wands', 'upright': '堅持、防禦、準備', 'reversed': '疲憊、防禦性、缺乏準備'},
                {'name': '權杖十 Ten of Wands', 'upright': '負擔、責任、壓力', 'reversed': '釋放負擔、缺乏責任、過度承擔'},
                {'name': '權杖侍者 Page of Wands', 'upright': '探索、熱情、自由', 'reversed': '缺乏方向、延遲、缺乏熱情'},
                {'name': '權杖騎士 Knight of Wands', 'upright': '行動、冒險、衝動', 'reversed': '延遲、缺乏方向、魯莽'},
                {'name': '權杖皇后 Queen of Wands', 'upright': '熱情、獨立、活力', 'reversed': '缺乏信心、依賴、缺乏熱情'},
                {'name': '權杖國王 King of Wands', 'upright': '領導、熱情、冒險', 'reversed': '衝動、缺乏耐心、專制'},
                {'name': '聖杯王牌 Ace of Cups', 'upright': '愛、情感、直覺', 'reversed': '情感封閉、缺乏愛、不安全感'},
                {'name': '聖杯二 Two of Cups', 'upright': '夥伴關係、和諧、愛', 'reversed': '分離、不和諧、缺乏愛'},
                {'name': '聖杯三 Three of Cups', 'upright': '慶祝、友誼、快樂', 'reversed': '過度放縱、孤獨、缺乏慶祝'},
                {'name': '聖杯四 Four of Cups', 'upright': '冥想、內省、重新評估', 'reversed': '新的機會、行動、重新參與'},
                {'name': '聖杯五 Five of Cups', 'upright': '失望、悲傷、遺憾', 'reversed': '接受、希望、新開始'},
                {'name': '聖杯六 Six of Cups', 'upright': '懷舊、純真、回憶', 'reversed': '活在過去、缺乏成長、天真'},
                {'name': '聖杯七 Seven of Cups', 'upright': '選擇、幻想、機會', 'reversed': '清晰、現實、缺乏選擇'},
                {'name': '聖杯八 Eight of Cups', 'upright': '離開、尋找、放棄', 'reversed': '猶豫、恐懼、缺乏行動'},
                {'name': '聖杯九 Nine of Cups', 'upright': '滿足、願望實現、快樂', 'reversed': '物質主義、缺乏滿足、過度放縱'},
                {'name': '聖杯十 Ten of Cups', 'upright': '和諧、家庭、圓滿', 'reversed': '家庭衝突、缺乏和諧、不完整'},
                {'name': '聖杯侍者 Page of Cups', 'upright': '創意、訊息、機會', 'reversed': '缺乏創意、壞消息、錯失機會'},
                {'name': '聖杯騎士 Knight of Cups', 'upright': '浪漫、提議、創意', 'reversed': '不切實際、缺乏行動、情感不穩定'},
                {'name': '聖杯皇后 Queen of Cups', 'upright': '同情、直覺、關懷', 'reversed': '情感依賴、缺乏邊界、過度敏感'},
                {'name': '聖杯國王 King of Cups', 'upright': '情感平衡、智慧、同理心', 'reversed': '情感不平衡、缺乏控制、冷漠'},
                {'name': '寶劍王牌 Ace of Swords', 'upright': '清晰、真理、突破', 'reversed': '混亂、謊言、缺乏清晰'},
                {'name': '寶劍二 Two of Swords', 'upright': '決策、平衡、僵局', 'reversed': '優柔寡斷、缺乏平衡、釋放'},
                {'name': '寶劍三 Three of Swords', 'upright': '心痛、悲傷、背叛', 'reversed': '治癒、寬恕、釋放痛苦'},
                {'name': '寶劍四 Four of Swords', 'upright': '休息、恢復、冥想', 'reversed': '缺乏休息、過度工作、重新開始'},
                {'name': '寶劍五 Five of Swords', 'upright': '衝突、失敗、損失', 'reversed': '和解、寬恕、避免衝突'},
                {'name': '寶劍六 Six of Swords', 'upright': '過渡、改變、離開', 'reversed': '停滯、缺乏改變、回歸'},
                {'name': '寶劍七 Seven of Swords', 'upright': '策略、秘密、逃避', 'reversed': '誠實、面對問題、缺乏策略'},
                {'name': '寶劍八 Eight of Swords', 'upright': '限制、恐懼、無助', 'reversed': '釋放、面對恐懼、新視角'},
                {'name': '寶劍九 Nine of Swords', 'upright': '焦慮、恐懼、噩夢', 'reversed': '釋放恐懼、希望、內在平靜'},
                {'name': '寶劍十 Ten of Swords', 'upright': '結束、痛苦、背叛', 'reversed': '恢復、新開始、釋放痛苦'},
                {'name': '寶劍侍者 Page of Swords', 'upright': '新想法、訊息、學習', 'reversed': '缺乏想法、壞消息、缺乏學習'},
                {'name': '寶劍騎士 Knight of Swords', 'upright': '行動、衝動、挑戰', 'reversed': '延遲、缺乏方向、魯莽'},
                {'name': '寶劍皇后 Queen of Swords', 'upright': '獨立、清晰、智慧', 'reversed': '冷酷、缺乏同情、過度分析'},
                {'name': '寶劍國王 King of Swords', 'upright': '權威、清晰、真理', 'reversed': '專制、缺乏同情、濫用權力'},
                {'name': '錢幣王牌 Ace of Pentacles', 'upright': '機會、繁榮、新開始', 'reversed': '錯失機會、缺乏繁榮、延遲'},
                {'name': '錢幣二 Two of Pentacles', 'upright': '平衡、適應、優先級', 'reversed': '不平衡、缺乏適應、混亂'},
                {'name': '錢幣三 Three of Pentacles', 'upright': '團隊合作、技能、成長', 'reversed': '缺乏合作、技能不足、缺乏成長'},
                {'name': '錢幣四 Four of Pentacles', 'upright': '安全、節儉、保護', 'reversed': '貪婪、缺乏安全、過度保護'},
                {'name': '錢幣五 Five of Pentacles', 'upright': '貧困、困難、孤立', 'reversed': '恢復、希望、新機會'},
                {'name': '錢幣六 Six of Pentacles', 'upright': '分享、慷慨、幫助', 'reversed': '自私、不平衡、依賴'},
                {'name': '錢幣七 Seven of Pentacles', 'upright': '耐心、等待、投資', 'reversed': '焦躁、失望、回報不足'},
                {'name': '錢幣八 Eight of Pentacles', 'upright': '努力、專注、學習', 'reversed': '疏忽、缺乏專注、半途而廢'},
                {'name': '錢幣九 Nine of Pentacles', 'upright': '獨立、成就、享受', 'reversed': '依賴、失敗、孤獨'},
                {'name': '錢幣十 Ten of Pentacles', 'upright': '財富、家庭、傳承', 'reversed': '損失、家庭糾紛、破產'},
                {'name': '錢幣侍者 Page of Pentacles', 'upright': '學習、機會、成長', 'reversed': '懶惰、錯失機會、缺乏目標'},
                {'name': '錢幣騎士 Knight of Pentacles', 'upright': '勤奮、責任、穩定', 'reversed': '拖延、固執、缺乏彈性'},
                {'name': '錢幣皇后 Queen of Pentacles', 'upright': '實際、溫暖、照顧', 'reversed': '過度保護、物質主義、忽略自我'},
                {'name': '錢幣國王 King of Pentacles', 'upright': '富有、穩重、成功', 'reversed': '貪婪、固執、失敗'}
            ]
            
            card = random.choice(tarot_cards)
            is_upright = random.choice([True, False])
            position = '正位' if is_upright else '逆位'
            meaning = card['upright'] if is_upright else card['reversed']

            # 取得用戶問題（去除「塔羅牌」關鍵字後的內容）
            question = content_after_prefix.split('塔羅牌', 1)[-1].strip()
            # 發送loading訊息
            loading_msg = await message.channel.send(f"{message.author.mention} 小青正在為你解讀牌卡意思")
            if question:
                prompt = (
                    f"你是一位專業塔羅牌解讀師。請根據用戶的問題，結合抽到的塔羅牌與正逆位，給出100到150字之間的詳細解讀，內容要有同理心、具體、貼近生活，並用繁體中文回答。\n"
                    f"用戶的問題：{question}\n"
                    f"抽到的牌：{card['name']} {position}\n"
                    f"這張牌的基本意義：{meaning}\n"
                    f"請開始詳細解讀（100-150字）："
                )
                response = client.chat.completions.create(
                    model="gpt-5.1",
                    messages=[{"role": "system", "content": "你是一位專業塔羅牌解讀師，請用繁體中文回答。"},
                              {"role": "user", "content": prompt}]
                )
                ai_reply = response.choices[0].message.content
                reply = f"{message.author.mention} 你抽到的塔羅牌是：{card['name']}（{position}）\n\n{ai_reply}"
            else:
                reply = f"{message.author.mention} 你抽到的塔羅牌是：{card['name']}（{position}）\n解釋：{meaning}"
            
            db_manager.add_chat(
                str(message.guild.id),
                str(message.author.id),
                message.author.name,
                content_after_prefix,
                reply
            )
            await loading_msg.edit(content=f"{message.author.mention} {reply}")
            return
        
        # 檢查是否為生成怪物命令
        if '生成怪物' in content_after_prefix:
            try:
                # 發送生成中訊息
                loading_msg = await message.channel.send(f"{message.author.mention} 獸潮正在來襲，請稍等...")
                
                # 獲取台灣時間
                taiwan_now = datetime.utcnow() + timedelta(hours=8)
                current_month_year = taiwan_now.strftime('%Y-%m')
                last_month_year = (taiwan_now.replace(day=1) - timedelta(days=1)).strftime('%Y-%m')
                
                # 清空上個月的未擊殺個人怪物
                db_manager.clear_monthly_monsters(str(message.guild.id), last_month_year)
                
                # 清空當前月份的未擊殺個人怪物（如果有的話，重新生成）
                db_manager.clear_monthly_monsters(str(message.guild.id), current_month_year)
                
                # 獲取身分組ID為1448281984949293138的成員數量
                role_id = 1448281984949293138
                role = message.guild.get_role(role_id)
                if not role:
                    await loading_msg.edit(content=f"{message.author.mention} 找不到指定的身分組（ID: {role_id}）")
                    return
                
                # 計算擁有該身分組的成員數量
                member_count = 0
                
                # 嘗試獲取成員數量（需要啟用 SERVER MEMBERS INTENT）
                try:
                    # 方法1：使用 role.members（需要 members intent）
                    try:
                        member_count = len(role.members)
                        if member_count > 0:
                            print(f"使用 role.members 獲得的成員數量: {member_count}")
                    except AttributeError:
                        print("無法使用 role.members（需要啟用 SERVER MEMBERS INTENT）")
                        # 如果沒有 members intent，嘗試其他方法
                        try:
                            # 嘗試使用 guild.members（也需要 members intent）
                            if hasattr(message.guild, 'members') and message.guild.members:
                                # 確保成員已載入
                                if not message.guild.chunked:
                                    print("正在載入伺服器成員...")
                                    try:
                                        await message.guild.chunk()
                                    except Exception:
                                        pass
                                
                                member_count = sum(1 for member in message.guild.members if role in member.roles)
                                print(f"通過遍歷成員獲得的數量: {member_count}")
                        except Exception as e:
                            print(f"獲取成員數量失敗: {e}")
                except Exception as e:
                    print(f"獲取成員數量時發生錯誤: {e}")
                
                # 如果無法獲取成員數量，使用預設值
                if member_count == 0:
                    print("警告：無法獲取身分組成員數量（需要啟用 SERVER MEMBERS INTENT），使用預設值 1")
                    member_count = 1  # 使用預設值，避免計算錯誤
                    await message.channel.send(
                        f"{message.author.mention} ⚠️ 注意：無法獲取身分組成員數量（需在 Discord 開發者門戶啟用 SERVER MEMBERS INTENT），將使用預設值進行計算。"
                    )
                
                # 獲取上個月的個人總擊殺數量
                last_month_total_kills = db_manager.get_total_personal_kills_last_month(
                    str(message.guild.id),
                    last_month_year
                )
                
                # 如果上個月擊殺數為0，則以1計算
                kill_multiplier = last_month_total_kills if last_month_total_kills > 0 else 1
                
                # 計算基礎血量：人數*3*2*(上個月個人總擊殺數量，若為0則以1計算)
                base_hp = member_count * 3 * 2 * kill_multiplier
                
                # 定義階級倍數：低階*1，中階*2，高階*3
                tier_multipliers = {
                    "低階": 1,
                    "中階": 2,
                    "高階": 3
                }
                
                print(f"上個月個人總擊殺數: {last_month_total_kills}, 倍數: {kill_multiplier}, 基礎血量: {base_hp}")
                
                # 獲取這個月的個人總擊殺數量
                current_month_total_kills = db_manager.get_total_personal_kills_current_month(
                    str(message.guild.id),
                    current_month_year
                )
                
                # 檢查是否為本月第一次輸入指令（團隊目標）
                target_count, killed_count = db_manager.get_team_goal(str(message.guild.id), current_month_year)
                
                if target_count is None:
                    # 本月第一次，設置團隊目標：人數*2
                    team_target = member_count * 2
                    db_manager.set_team_goal(str(message.guild.id), team_target, current_month_year)
                    target_count = team_target
                    killed_count = 0
                
                # 生成三隻怪物
                monsters = await monster_service.generate_monster()
                
                # 將怪物存入資料庫，根據階級計算不同血量
                for monster in monsters:
                    tier = monster["tier"]
                    tier_multiplier = tier_multipliers.get(tier, 1)
                    monster_hp = base_hp * tier_multiplier
                    
                    db_manager.add_monster(
                        str(message.guild.id),
                        monster["name"],
                        tier,
                        "",  # 不再顯示外型
                        monster_hp,
                        'personal'
                    )
                    
                    # 在怪物資料中添加血量資訊，方便後續顯示
                    monster["hp"] = monster_hp
                
                # 統一顯示完整格式
                # 格式化台灣時間
                taiwan_time_str = taiwan_now.strftime('%Y年%m月%d日 %H:%M')
                
                response_text = f"**{taiwan_time_str}**\n\n"
                response_text += f"當前玩家人數：{member_count}\n"
                response_text += f"上個月個人總擊殺量：{last_month_total_kills}\n"
                response_text += f"這個月個人總擊殺量：{current_month_total_kills}\n\n"
                response_text += "**個人目標**\n"
                
                # 按照低中高階順序顯示
                tier_order = ["低階", "中階", "高階"]
                for tier in tier_order:
                    for monster in monsters:
                        if monster['tier'] == tier:
                            response_text += f"{tier}怪物：{monster['name']}（血量：{monster['hp']}）\n"
                            break
                
                response_text += "\n**團隊目標**\n"
                response_text += f"已擊殺數量：{killed_count} / {target_count}\n\n"
                
                response_text += "使用「小青!(怪物名稱)(空格)(傷害值)」來攻擊怪物！"
                
                # 編輯訊息
                await loading_msg.edit(content=f"{message.author.mention} {response_text}")
                return
                
            except Exception as e:
                await message.channel.send(f"{message.author.mention} 生成怪物時發生錯誤：{str(e)}")
                import traceback
                traceback.print_exc()
                return
        
        # 檢查是否為攻擊怪物命令（格式：小青!(怪物名稱) (數字)）
        # 嘗試解析命令
        import re
        attack_match = re.match(r'^(.+?)\s+(\d+)$', content_after_prefix)
        if attack_match:
            monster_name = attack_match.group(1).strip()
            damage = int(attack_match.group(2))
            
            try:
                # 檢查怪物是否存在
                monster_data = db_manager.get_monster(str(message.guild.id), monster_name)
                
                if not monster_data:
                    await message.channel.send(f"{message.author.mention} 找不到名為「{monster_name}」的怪物。")
                    return
                
                # 攻擊怪物
                new_hp, error = db_manager.attack_monster(
                    str(message.guild.id),
                    monster_name,
                    str(message.author.id),
                    message.author.name,
                    damage
                )
                
                if error:
                    await message.channel.send(f"{message.author.mention} {error}")
                    return
                
                # 構建回應
                if new_hp > 0:
                    response = f"{message.author.mention} 對 **{monster_name}** 造成了 **{damage}** 點傷害！\n"
                    response += f"剩餘血量：**{new_hp}** HP"
                    await message.channel.send(response)
                else:
                    # 怪物被擊敗
                    # 獲取台灣時間和當前月份
                    taiwan_now = datetime.utcnow() + timedelta(hours=8)
                    current_month_year = taiwan_now.strftime('%Y-%m')
                    
                    # 增加個人擊殺數（只計算最後一擊的玩家）
                    db_manager.increment_personal_kills(
                        str(message.guild.id),
                        str(message.author.id),
                        message.author.name,
                        current_month_year
                    )
                    
                    # 增加團隊擊殺數
                    db_manager.increment_team_kills(str(message.guild.id), current_month_year)
                    
                    # 獲取統計數據
                    personal_kills = db_manager.get_personal_kills(
                        str(message.guild.id),
                        str(message.author.id),
                        current_month_year
                    )
                    target_count, killed_count = db_manager.get_team_goal(
                        str(message.guild.id),
                        current_month_year
                    )
                    
                    # 生成誇獎句子
                    try:
                        praise_prompt = f"請為擊敗怪物「{monster_name}」的勇者們創作一句簡短的誇獎句子（30字內），要熱血且鼓舞人心，用繁體中文回答。"
                        praise_response = client.chat.completions.create(
                            model="gpt-4o-mini",
                            messages=[
                                {"role": "system", "content": "你是一位遊戲旁白，擅長創作熱血的誇獎句子。"},
                                {"role": "user", "content": praise_prompt}
                            ]
                        )
                        praise_text = praise_response.choices[0].message.content.strip()
                    except:
                        praise_text = "真是太厲害了！"
                    
                    response = f"🎉 **{monster_name}** 被擊敗了！\n\n"
                    response += f"{praise_text}\n\n"
                    response += f"**統計資訊**\n"
                    response += f"個人擊殺數：{personal_kills} 隻\n"
                    response += f"團隊已擊殺：{killed_count} / {target_count} 隻"
                    
                    await message.channel.send(response)
                
                return
                
            except Exception as e:
                await message.channel.send(f"{message.author.mention} 攻擊怪物時發生錯誤：{str(e)}")
                return
        
        # 檢查是否為故事生成查詢
        story_keywords = ['說', '字', '故事']
        is_story_query = any(keyword in content_after_prefix for keyword in story_keywords) and '字' in content_after_prefix and '故事' in content_after_prefix
        
        if is_story_query:
            try:
                # 提取故事資訊
                word_count, story_type = story_service.extract_story_info(content_after_prefix)
                
                # 檢查字數限制（避免生成過長的故事）
                if word_count > 10000:
                    await message.channel.send(f"{message.author.mention} 抱歉，故事字數不能超過一千字，請重新指定較少的字數。")
                    return
                
                # 發送生成中訊息
                search_msg = await message.channel.send(f"{message.author.mention} 小青正在創作{word_count}字的{story_type}故事，請稍等一下...")
                
                # 生成故事提示詞
                story_prompt = story_service.generate_story_prompt(word_count, story_type)
                
                # 調用 OpenAI API 生成故事
                response = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[
                        {"role": "system", "content": "你是一位專業的故事創作者，擅長創作各種類型的故事。請用繁體中文回答。"},
                        {"role": "user", "content": story_prompt}
                    ],
                    max_tokens=16000  # 大幅增加 token 限制以生成更長的故事
                )
                
                # 獲取生成的故事
                generated_story = response.choices[0].message.content
                
                # 構建故事訊息
                story_message = f"**{story_type}故事**\n\n{generated_story}\n\n---\n*字數：約{word_count}字*"
                
                # 儲存對話記錄
                db_manager.add_chat(
                    str(message.guild.id),
                    str(message.author.id),
                    message.author.name,
                    content_after_prefix,
                    story_message
                )
                
                # 編輯訊息顯示故事
                await search_msg.edit(content=f"{message.author.mention} {story_message}")
                return
            
            except Exception as e:
                await message.channel.send(f"{message.author.mention} 抱歉，故事生成失敗：{str(e)}")
                return

    # 處理其他命令
    await bot.process_commands(message)

# 繪圖相關功能
async def generate_image(prompt):
    try:
        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="hd",
            style="vivid",
            n=1,
        )
        return response.data[0].url
    except Exception as e:
        print(f"生成圖片時發生錯誤: {e}")
        raise e

async def download_image(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status == 200:
                try:
                    # 檢查磁碟空間
                    total, used, free = shutil.disk_usage('.')
                    if free < 50 * 1024 * 1024:  # 少於 50MB
                        raise Exception(f"磁碟空間不足，可用空間: {free / 1024 / 1024:.1f}MB")
                    
                    # 檢查臨時目錄權限
                    temp_dir = tempfile.gettempdir()
                    if not os.access(temp_dir, os.W_OK):
                        raise Exception(f"沒有權限寫入臨時目錄: {temp_dir}")
                    
                    # 創建臨時文件
                    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
                        content = await response.read()
                        tmp_file.write(content)
                        tmp_file.flush()  # 確保數據寫入磁碟
                        os.fsync(tmp_file.fileno())  # 強制同步到磁碟
                        tmp_file.close()
                        
                        # 驗證文件是否成功創建
                        if not os.path.exists(tmp_file.name):
                            raise Exception("臨時文件創建失敗")
                        
                        print(f"成功下載圖片到: {tmp_file.name}")
                        return tmp_file.name
                except Exception as e:
                    print(f"創建臨時文件失敗: {e}")
                    raise Exception(f"無法創建臨時文件: {e}")
            else:
                raise Exception(f"下載圖片失敗: {response.status}")

# 修改繪圖命令
@bot.command(name='draw')
async def draw(ctx, *, prompt):
    wait_msg = None
    image_path = None
    try:
        async with ctx.typing():
            # 發送等待消息
            wait_msg = await ctx.send(f"{ctx.author.mention} 小青正在畫畫～請稍等一下！")
            
            # 檢查系統資源
            try:
                memory = psutil.virtual_memory()
                if memory.percent > 90:
                    print(f"警告：記憶體使用率過高 ({memory.percent}%)")
                
                disk = psutil.disk_usage('.')
                if disk.free < 100 * 1024 * 1024:  # 少於 100MB
                    print(f"警告：磁碟空間不足 ({disk.free / 1024 / 1024:.1f}MB)")
            except Exception as e:
                print(f"檢查系統資源時發生錯誤: {e}")
            
            # 生成圖片
            image_url = await generate_image(prompt)
            
            # 下載圖片
            image_path = await download_image(image_url)
            
            # 檢查文件是否存在
            if not os.path.exists(image_path):
                raise Exception("生成的圖片文件不存在")
            
            # 檢查文件權限
            if not os.access(image_path, os.R_OK):
                raise Exception("無法讀取生成的圖片文件")
            
            # 檢查文件大小
            file_size = os.path.getsize(image_path)
            if file_size == 0:
                raise Exception("生成的圖片文件為空")
            
            print(f"圖片文件大小: {file_size / 1024:.1f}KB")
            
            # 發送圖片
            try:
                with open(image_path, 'rb') as f:
                    file = discord.File(f, filename='generated_image.png')
                    await ctx.send(f"{ctx.author.mention} 已完成繪圖！\n提示詞：{prompt}", file=file)
                    print("圖片發送成功")
            except PermissionError:
                raise Exception("沒有權限讀取圖片文件")
            except Exception as e:
                print(f"發送圖片失敗: {e}")
                raise Exception(f"無法發送圖片: {e}")
            
            # 刪除等待消息
            if wait_msg:
                await wait_msg.delete()
            
    except Exception as e:
        print(f"繪圖命令執行錯誤: {e}")
        if wait_msg:
            try:
                await wait_msg.delete()
            except:
                pass
        await ctx.send(f"{ctx.author.mention} 繪圖時發生錯誤：{str(e)}")
    finally:
        # 確保清理臨時文件
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
                print(f"成功刪除臨時文件: {image_path}")
            except Exception as e:
                print(f"刪除臨時文件失敗: {e}")

# 運行機器人
try:
    print("正在啟動機器人...")
    print("檢查系統資源...")
    
    # 最終系統檢查
    try:
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage('.')
        print(f"記憶體使用率: {memory.percent}%")
        print(f"磁碟使用率: {disk.percent}%")
        print(f"可用磁碟空間: {disk.free / 1024 / 1024:.1f}MB")
    except Exception as e:
        print(f"系統檢查失敗: {e}")
    
    bot.run(discord_token)
except KeyboardInterrupt:
    print("正在關閉機器人...")
    db_manager.close()
    print("機器人已關閉")
except Exception as e:
    print(f"機器人運行錯誤: {e}")
    print("嘗試關閉資料庫連接...")
    try:
        db_manager.close()
    except Exception as close_error:
        print(f"關閉資料庫連接時發生錯誤: {close_error}")
    print("機器人已停止")
finally:
    try:
        db_manager.close()
        print("資料庫連接已關閉")
    except Exception as e:
        print(f"關閉資料庫連接時發生錯誤: {e}") 