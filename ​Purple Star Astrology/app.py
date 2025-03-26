from flask import Flask, render_template, request, session, redirect, url_for, send_file, jsonify
from flask_cors import CORS  # 导入 CORS
from llmana.glmapi import GLMClient
from llmana.deepseek_ali_api import DeepSeekClient
from json2ziwei.api import SolarAPI
from json2ziwei.convert import convert_main_json_to_text
from llmana.deepseek_huoshan_api import deepseek_huoshan
import os
import threading
import sqlite3
from datetime import datetime
import time  # 添加 time 模块
from dotenv import load_dotenv  # 添加 dotenv 支持
from token_ana.deepseek_tokenizer import initialize_tokenizer, encode_text

# 加载 .env 文件
load_dotenv()

app = Flask(__name__)
app.secret_key = '9957'  # 用于会话加密
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5000"}})

class StandardizedLLMClient:
    """
    @class StandardizedLLMClient
    @description 标准化大模型客户端接口
    """
    def __init__(self):
        """
        @constructor
        @description 初始化客户端，从环境变量读取配置
        """
        self.api_key = os.getenv('ARK_API_KEY')

        self.client = deepseek_huoshan(self.api_key)
        self.tokenizer = initialize_tokenizer()  # 初始化 tokenizer

    def get_response(self, prompt):
        """
        @method get_response
        @description 获取大模型响应
        @param {str} prompt - 输入提示
        @returns {tuple} - (响应结果, token数量)
        """
        # 计算输入 token 数量
        input_tokens = len(encode_text(prompt, self.tokenizer))
        
        # 获取模型响应
        response = self.client.get_response(prompt)
        
        # 计算输出 token 数量
        output_tokens = len(encode_text(response, self.tokenizer))
        
        return response, input_tokens + output_tokens

# 在需要的地方使用标准化接口
llm_client = StandardizedLLMClient()

@app.route('/', methods=['GET', 'POST'])
def index():
    text_description = ""
    if request.method == 'POST':
        date = request.form.get('date')
        timezone = request.form.get('timezone')
        gender = request.form.get('gender')
        calendar = request.form.get('calendar')  # 获取历法选择

        # 打印调试信息
        print(f"日期: {date}, 时区: {timezone}, 性别: {gender}, 历法: {calendar}")

        # 调用 SolarAPI 获取星盘数据
        solar_api = SolarAPI("http://localhost:3000")
        try:
            json_string = solar_api.get_astrolabe_data(date, int(timezone), gender, is_solar=(calendar == 'solar'))
        except Exception as e:
            text_description = f"请求错误: {e}"
        else:
            main_data = json_string
            text_description = convert_main_json_to_text(main_data)
            session['date'] = date
            session['timezone'] = timezone
            session['gender'] = gender
            session['calendar'] = calendar
            session['text_description'] = text_description

            return redirect(url_for('fortune_telling'))  # 跳转到算命解析页面

    return render_template('index.html', text_description=text_description)

def init_db():
    """初始化数据库表"""
    conn = sqlite3.connect('data.db')  # 使用 data.db 作为数据库文件名
    cursor = conn.cursor()
    
    # 创建 results 表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            analysis_type TEXT NOT NULL,
            data TEXT NOT NULL,
            date TEXT NOT NULL,
            timezone TEXT NOT NULL,
            gender TEXT NOT NULL,
            calendar TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# 在应用启动时初始化数据库
init_db()

###

@app.route('/fortune_telling', methods=['GET'])
def fortune_telling():
    # 从 session 获取数据
    date = session.get('date')
    timezone = session.get('timezone')
    gender = session.get('gender')
    calendar = session.get('calendar')
    text_description = session.get('text_description')

    # 检查是否有缓存结果
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT analysis_type, data 
        FROM results 
        WHERE date = ? AND timezone = ? AND gender = ? AND calendar = ?
        ORDER BY timestamp DESC
    """, (date, timezone, gender, calendar))
    
    cached_results = {}
    execution_times = {}
    
    for row in cursor.fetchall():
        analysis_type, result = row
        cached_results[analysis_type] = result
        match = result.split('\n')[0].strip()
        if match.startswith('推理耗时:'):
            try:
                time_str = match.split(':')[1].strip()
                execution_times[analysis_type] = float(time_str.replace('秒', ''))
            except (IndexError, ValueError):
                execution_times[analysis_type] = 0
    
    conn.close()
    
    # 如果有缓存结果，直接返回
    if cached_results:
        return render_template('fortune_telling.html', 
                             date=date, 
                             timezone=timezone, 
                             gender=gender,
                             calendar=calendar, 
                             text_description=text_description, 
                             fortune_results=cached_results,
                             execution_times=execution_times,
                             ming_gong=cached_results.get('命宫', '暂无命宫分析结果'))  # 确保命宫数据传递正确

    # 如果没有缓存结果，进行新的分析
    analysis_types = [
        "full_analysis", "命宫", "兄弟宫", "夫妻宫", "子女宫", "财帛宫",
        "疾厄宫", "迁移宫", "仆役宫", "官禄宫", "田宅宫", "福德宫", "父母宫",
        "marriage_path", "challenges", "partner_character"
    ]

    results = {}
    threads = []
    execution_times = {}  # 用于存储每个分析类型的执行时间

    def analyze_thread(analysis_type):
        start_time = time.time()
        analysis_prompts = {
            "full_analysis": "请对整个紫微斗数命盘进行全面分析，包括事业、财运、健康、婚姻、家庭、性格、学业、子女等方面，并提供具体建议。命盘如下：  \n",
            "命宫": "请分析命宫，解释该宫位对命主的影响及性格特点。命盘如下：  \n",
            # 其他分析类型...
        }

        if analysis_type in analysis_prompts:
            prompt = analysis_prompts[analysis_type] + text_description
            response, token_count = llm_client.get_response(prompt)
        else:
            response, token_count = "无效分析类型", 0

        end_time = time.time()
        execution_time = round(end_time - start_time, 2)

        results[analysis_type] = {
            'response': response,
            'execution_time': execution_time,
            'token_count': token_count
        }

    for analysis_type in analysis_types:
        thread = threading.Thread(target=analyze_thread, args=(analysis_type,))
        threads.append(thread)
        thread.start()

    for thread in threads:
        thread.join()

    for analysis_type, result in results.items():
        results[analysis_type] = f"推理耗时: {result['execution_time']}秒\nToken 数量: {result['token_count']}\n\n{result['response']}"

    # 将结果存储到 SQLite 数据库
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    for analysis_type, fortune_result in results.items():
        cursor.execute("""
            INSERT INTO results 
            (analysis_type, data, date, timezone, gender, calendar) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (analysis_type, fortune_result, date, timezone, gender, calendar))
    conn.commit()
    conn.close()

    return render_template('fortune_telling.html', 
                         date=date, 
                         timezone=timezone, 
                         gender=gender,
                         calendar=calendar, 
                         text_description=text_description, 
                         fortune_results=results,
                         execution_times=execution_times,
                         ming_gong=results.get('命宫', '暂无命宫分析结果'))  # 确保命宫数据传递正确

@app.route('/download_md')
def download_md():
    # 获取文件名并确保路径中没有不可见字符
    filename = f"fortune_result_{session.get('date')}_{session.get('timezone')}.md".replace('\u200b', '')
    filepath = os.path.join(os.getcwd(), filename)
    
    # 检查文件是否存在，如果不存在则生成新的 Markdown 文件
    if not os.path.exists(filepath):
        generate_markdown(session.get('fortune_results', {}))
    
    if not os.path.exists(filepath):
        return "文件不存在", 404
    
    return send_file(filepath, as_attachment=True)

def generate_markdown(fortune_results):
    """
    @function generate_markdown
    @description 生成包含推理统计信息的 Markdown 文件
    @param {dict} fortune_results - 包含推理结果、时间和 token 数量的字典
    """
    markdown_content = "# 紫微斗数算命结论\n\n"

    # 添加命宫分析结果
    if '命宫' in fortune_results:
        markdown_content += "## 命宫分析\n"
        markdown_content += fortune_results['命宫'] + "\n\n"

    # 添加其他宫位分析结果
    for analysis_type, result in fortune_results.items():
        if analysis_type != '命宫':  # 命宫已单独处理
            markdown_content += f"## {analysis_type.replace('_', ' ').title()}\n"
            lines = result.split('\n')
            time_line = lines[0] if len(lines) > 0 else ""
            token_line = lines[1] if len(lines) > 1 else ""
            content = '\n'.join(lines[2:]) if len(lines) > 2 else ""
            markdown_content += f"{time_line}\n"
            markdown_content += f"{token_line}\n"
            markdown_content += f"\n{content}\n\n"

    # 生成文件名并确保路径中没有不可见字符
    filename = f"fortune_result_{session.get('date')}_{session.get('timezone')}.md".replace('\u200b', '')
    filepath = os.path.join(os.getcwd(), filename)

    # 写入文件
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown_content)

@app.route('/download/<analysis_type>')
def download_result(analysis_type):
    """下载特定类型的分析结果"""
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute("""
        SELECT data FROM results 
        WHERE analysis_type = ? 
        AND date = ? AND timezone = ? AND gender = ? AND calendar = ?
        ORDER BY timestamp DESC LIMIT 1
    """, (analysis_type, session.get('date'), session.get('timezone'), 
          session.get('gender'), session.get('calendar')))
    
    result = cursor.fetchone()
    conn.close()
    
    if result:
        # 创建临时文件
        filename = f"{analysis_type}_result.md"
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(f"# {analysis_type.replace('_', ' ').title()} 分析结果\n\n{result[0]}")
        
        return send_file(filename, as_attachment=True)
    
    return "No result found", 404

@app.route('/analyze', methods=['POST'])
def analyze():
    # 获取表单数据
    palace = request.form.get('palace')
    
    # 从 session 获取之前存储的命盘数据
    text_description = session.get('text_description')
    
    # 定义分析提示
    analysis_prompts = {
        "命宫": "请分析命宫，解释该宫位对命主的影响及性格特点。命盘如下：  \n",
        "兄弟宫": "请分析兄弟宫，解释该宫位对命主手足缘分、人际关系的影响。命盘如下：  \n",
        "夫妻宫": "请分析夫妻宫，解释该宫位对命主婚姻、感情的影响。命盘如下：  \n",
        "子女宫": "请分析子女宫，解释该宫位对命主子女运势的影响。命盘如下：  \n",
        "财帛宫": "请分析财帛宫，解释该宫位对命主财富、金钱运势的影响。命盘如下：  \n",
        "疾厄宫": "请分析疾厄宫，解释该宫位对命主健康、疾病运势的影响。命盘如下：  \n",
        "迁移宫": "请分析迁移宫，解释该宫位对命主外出、旅行、迁移的影响。命盘如下：  \n",
        "仆役宫": "请分析仆役宫，解释该宫位对命主朋友、合作关系的影响。命盘如下：  \n",
        "官禄宫": "请分析官禄宫，解释该宫位对命主事业、工作运势的影响。命盘如下：  \n",
        "田宅宫": "请分析田宅宫，解释该宫位对命主家庭、房产运势的影响。命盘如下：  \n",
        "福德宫": "请分析福德宫，解释该宫位对命主福气、精神世界的影响。命盘如下：  \n",
        "父母宫": "请分析父母宫，解释该宫位对命主与父母的关系、祖荫运势的影响。命盘如下：  \n"
    }
    
    # 获取对应的分析提示
    prompt = analysis_prompts.get(palace, "无效宫位类型") + text_description
    
    # 调用大模型获取分析结果
    response, _ = llm_client.get_response(prompt)
    
    # 返回分析结果
    return jsonify({'result': response})

@app.route('/analyze_time', methods=['POST'])
def analyze_time():
    # 获取表单数据
    time_type = request.form.get('timeType')
    selected_date = request.form.get('selectedDate')
    concern = request.form.get('concern', '').strip()  # 获取关心的事务，默认为空
    
    # 从 session 获取之前存储的命盘数据
    text_description = session.get('text_description')
    
    # 定义分析提示
    analysis_prompts = {
        "大限": f"请分析大限，解释该时间段对命主整体运势的影响。{'特别关注：' + concern if concern else ''} 命盘如下：  \n 选择日期（农历）: {selected_date}\n",
        "流年": f"请结合大限信息,分析流年，解释该年份对命主运势的影响。{'特别关注：' + concern if concern else ''} 命盘如下：  \n 选择日期（农历）: {selected_date}\n",
        "流月": f"请结合大限,流年信息分析流月，解释该月份对命主运势的影响。{'特别关注：' + concern if concern else ''} 命盘如下：  \n 选择日期（农历）: {selected_date}\n",
        "流日": f"请结合大限,流年,流月信息分析流日，解释该日期对命主运势的影响。{'特别关注：' + concern if concern else ''} 命盘如下：  \n 选择日期（农历）: {selected_date}\n"
    }
    
    # 获取对应的分析提示
    prompt = analysis_prompts.get(time_type, "无效时间类型") + text_description
    
    # 调用大模型获取分析结果
    response, _ = llm_client.get_response(prompt)
    
    # 返回分析结果
    return jsonify({'result': response})

if __name__ == '__main__':
    app.run(debug=True) 