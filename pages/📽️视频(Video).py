import os
import toml
import time
import torch
import datetime
import streamlit as st
from utils.utils import (get_whisper_result, kimi_translate, openai_translate1, openai_translate2,google_translate,
                         generate_srt_from_result, srt_mv, srt_to_vtt, srt_to_ass, srt_to_stl, show_video,
                         parse_srt_file, convert_to_srt)

project_dir = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")
cache_dir = project_dir + "/cache/"  # 本地缓存
config_dir = project_dir.replace("/pages", "") + "/config/"  # 配置文件

# 加载配置
config = toml.load(config_dir + "config.toml")
openai_api_key = config["GPT"]["openai_key"]
openai_api_base = config["GPT"]["openai_base"]
kimi_api_key = config["KIMI"]["kimi_key"]
local = config["WHISPER_LOCAL"]["local"]
model_local_path = config["WHISPER_LOCAL"]["model_local_path"]
whisper_version = config["WHISPER"]["whisper_version_default"]
whisper_model = config["WHISPER"]["whisper_model_default"]
st.session_state.openai_base = openai_api_base
st.session_state.openai_key = openai_api_key
st.session_state.kimi_key = kimi_api_key
st.session_state.local = local
st.session_state.model_local_path = model_local_path
st.session_state.w_model_option = whisper_model
st.session_state.w_name = whisper_version

st.set_page_config(page_title="AI全自动视频翻译", page_icon="📽️", layout="wide", initial_sidebar_state="expanded")
st.title("AI全自动视频翻译📽️")
st.write("")
with st.sidebar:
    # 文件上传
    st.write("### 文件上传器")
    uploaded_file = st.file_uploader("请在这里上传视频：", type=['mp4', 'mov'], label_visibility="collapsed")
    if uploaded_file is not None:  # 判断是否上传成功
        st.write("文件类型:", uploaded_file.type)
        st.success("上传成功！")

col1, col2 = st.columns(2, gap="medium")
with col1:
    with st.expander("**识别设置**", expanded=True):
        # GPU
        GPU_on = st.toggle('启用GPU加速*', help='自动检测cuda、pytorch可用后开启！')
        device = 'cuda' if GPU_on else 'cpu'
        # VAD
        VAD_on = st.toggle('启用VAD辅助*', help='启用语音活动检测（VAD）以过滤掉没有语音的音频部分,仅支持faster-whisper使用。')
        vad = 'True' if GPU_on else 'False'
        # language
        language = ('自动识别', 'zh', 'en', 'ja', 'ko', 'it', 'de')
        lang = st.selectbox('选择视频语言', language, index=0, help="强制指定视频语言会提高识别准确度，但也可能会造成识别出错。")

    with st.expander("**翻译设置**", expanded=True):
        translate_option = st.selectbox('选择翻译引擎', ('kimi-moonshot-v1-8k', 'kimi-moonshot-v1-32k', 'kimi-moonshot-v1-128k', 'gpt-3.5-turbo', 'gpt-4', 'google', '无需翻译'), index=0)
        if translate_option != '无需翻译':
            language = ('中文', 'English', '日本語', '한국인', 'Italiano', 'Deutsch')
            col3, col4 = st.columns(2)
            with col3:
                language1 = st.selectbox('选择原始语言', language, index=1)
            with col4:
                language2 = st.selectbox('选择目标语言', language, index=0)
            proxy_on = st.toggle('启用代理', help='如果你能直接访问openai.com，则无需启用。')

    with st.expander("**字幕设置**", expanded=True):
        with open(project_dir.replace("/pages", "/config") + '/font_data.txt', 'r', encoding='utf-8') as file:
            lines = file.readlines()
            fonts = [line.strip() for line in lines]
            font = st.selectbox('视频字幕字体：', fonts, help="所有字体均从系统读取加载，支持用户自行安装字体。请注意商用风险！")
            col3, col4 = st.columns([0.9, 0.1],gap="medium")
            with col3:
                font_size = st.number_input('字幕字体大小', min_value=1, max_value=30, value=18, step=1, help="推荐大小：18")
            with col4:
                font_color = st.color_picker('颜色', '#FFFFFF')
with col2:
    with st.expander("**高级功能**"):
        token_num = st.number_input('翻译最大token限制', min_value=10, max_value=500, value=100, step=10)
        min_vad = st.number_input('VAD静音检测(ms)', min_value=100, max_value=5000, value=500, step=100,
                                  help="启用VAD辅助后生效！对应`min_silence_duration_ms`参数，最小静音持续时间。")
        beam_size = st.number_input('束搜索大小', min_value=1, max_value=20, value=5, step=1,
                                    help="`beam_size`参数。用于定义束搜索算法中每个时间步保留的候选项数量。束搜索算法通过在每个时间步选择最有可能的候选项来构建搜索树，并根据候选项的得分进行排序和剪枝。较大的beam_size值会保留更多的候选项，扩大搜索空间，可能提高生成结果的准确性，但也会增加计算开销。相反，较小的beam_size值会减少计算开销，但可能导致搜索过早地放弃最佳序列。")

with col1:
    if st.button('生成视频', type="primary", use_container_width=True):
        if uploaded_file is not None:

            time1 = time.time()
            current_time = datetime.datetime.now().strftime("%Y-%m-%d")
            raw_file_name=uploaded_file.name[:-4]
            output_path = cache_dir+current_time+'/' + str(hash(raw_file_name)%100000)
            raw_file_path = output_path + "/raw.mp4"
            whisper_file_path = output_path +"/gen.whisper"
            subtitle_file_path = output_path +"/gen.srt"
            generate_video_file = output_path +"/gen.mp4"

            with st.spinner('正在加载视频...'):
                
                if not os.path.exists(output_path):
                    os.makedirs(output_path)
                
                if os.path.exists(raw_file_path):
                    st.spinner('文件已经存在...')
                else:
                    with open(raw_file_path, "wb") as file:
                        file.write(uploaded_file.getbuffer())
            time2 = time.time()
            with st.spinner('正在识别视频内容...'):
                if os.path.isfile(whisper_file_path):
                    with open(whisper_file_path, "r", encoding="utf-8") as f:
                        raw_result = f.read()
                        result = eval(raw_result)
                    st.spinner("从缓存中读取到whisper的结果：")
                else:
                    models_option = st.session_state.w_model_option
                    if st.session_state.local:
                        models_option = st.session_state.model_local_path
                    result = get_whisper_result(uploaded_file, output_path, device, models_option,
                                                st.session_state.w_name, vad, lang, beam_size, min_vad)
                    print("whisper识别：" + result['text'])

                    # 将结果写入文件
                    with open(whisper_file_path, "w", encoding="utf-8") as f:
                        f.write(str(result))



            time3 = time.time()
            if translate_option != '无需翻译':
                if not os.path.isfile(subtitle_file_path):
                    with st.spinner('正在翻译文本...'):
                        if translate_option == 'gpt-3.5-turbo':
                            result = openai_translate1(st.session_state.openai_key, st.session_state.openai_base,
                                                    proxy_on, result, language1, language2)
                        elif translate_option == 'gpt-4':
                            result = openai_translate2(st.session_state.openai_key, st.session_state.openai_base,
                                                    proxy_on, result, language1, language2, token_num)
                        elif translate_option == 'google':
                            result = google_translate(result, language1, language2)
                        else:
                            result = kimi_translate(st.session_state.kimi_key, translate_option, result, language1, language2, token_num)
            time4 = time.time()
            with st.spinner('正在生成SRT字幕文件...'):
                srt_content = generate_srt_from_result(result)
                with open(subtitle_file_path, 'w', encoding='utf-8') as srt_file:
                    srt_file.write(srt_content)

            time5 = time.time()
            with st.spinner('正在合并视频，请耐心等待视频生成...'):
                srt_mv(output_path,raw_file_path,subtitle_file_path,generate_video_file)

            time6 = time.time()
            st.session_state.srt_content = srt_content
            st.session_state.output = output_path
            st.session_state.output_file = generate_video_file
            st.session_state.current = current_time
            st.session_state.time = time6 - time1
        else:
            st.warning("请先上传视频")

with col2:
    with st.expander("**视频预览**", expanded=True):
        try:
            video_bytes = show_video(st.session_state.output_file)
            st.video(video_bytes)
            formatted_result = f"{st.session_state.time:.2f}"
            st.success(f"合并成功！总用时：{formatted_result}秒")
            if st.button('查看文件目录', use_container_width=True):
                os.startfile(st.session_state.output)
                st.warning("注意：文件夹已成功打开，可能未置顶显示，请检查任务栏！")
        except:
            st.success('''
            **这里是视频预览窗口**                             
            **运行后自动显示预览结果**   
            ###### 详细步骤
            1. **配置设置：** 在主页-设置中，选择适合您需求的识别模型和翻译引擎。
            2. **上传文件：** 在侧栏的文件上传器中，上传您要转换的视频文件。
            3. **调整参数：** 在页面左侧调整视频生成的相关参数，您也可以根据需要配置高级功能。
            4. **生成视频：** 点击生成视频按钮，等待生成完成。
            ###### 以下可跳过
            5. **字幕校对：** 生成完成后，您可以在下方查看字幕内容并进行二次校对。
            6. **更多格式：** 按照需要，下载其他的字幕格式。
            7. **再次生成：** 在下方设置重新参数后，再次生成视频。                            
            ''')

st.write('''------''')
st.write('**字幕时间轴**(运行后自动显示)')
try:
    srt_data1 = parse_srt_file(st.session_state.srt_content)
    edited_data = st.data_editor(srt_data1, height=300, hide_index=True, use_container_width=True)
    srt_data2 = convert_to_srt(edited_data)
    st.session_state.srt_content_new = srt_data2
except:
    srt_data = [{"index": "", "start": "", "end": "", "content": ""}]
    edited_data = st.data_editor(srt_data, height=300, hide_index=True, use_container_width=True)
st.write('''
------
##### 实验功能🧪
''')
st.caption("运行程序后自动显示，实际可能会有BUG，后续版本会逐步完善并实装！")

col1, col2 = st.columns(2, gap="medium")
with col1:
    with st.expander("**更多字幕格式**", expanded=True):
        try:
            captions_option = st.radio('字幕导出格式：', ('srt', 'vtt', 'ass', 'stl'), index=0, horizontal=True)
            if captions_option == 'srt':
                st.download_button(
                    label="点击下载SRT字幕文件",
                    data=st.session_state.srt_content_new.encode('utf-8'),
                    key='srt_download',
                    file_name='output.srt',
                    mime='text/srt',
                    type="primary",
                    use_container_width=True
                )
            elif captions_option == 'vtt':
                vtt_content = srt_to_vtt(st.session_state.srt_content_new)
                st.download_button(
                    label="点击下载VTT字幕文件",
                    data=vtt_content.encode('utf-8'),
                    key='vtt_download',
                    file_name='output.vvt',
                    mime='text/vtt',
                    type="primary",
                    use_container_width=True
                )
            elif captions_option == 'ass':
                ass_content = srt_to_ass(st.session_state.srt_content_new)
                st.download_button(
                    label="点击下载ASS字幕文件",
                    data=ass_content.encode('utf-8'),
                    key='ass_download',
                    file_name='output.ass',
                    mime='text/ass',
                    type="primary",
                    use_container_width=True
                )
            elif captions_option == 'stl':
                stl_content = srt_to_stl(st.session_state.srt_content_new)
                st.download_button(
                    label="点击下载STL字幕文件",
                    data=stl_content.encode('utf-8'),
                    key='stl_download',
                    file_name='output.stl',
                    mime='text/stl',
                    type="primary",
                    use_container_width=True
                )
        except:
            st.warning('这里是字幕窗口，运行后自动显示下载按钮。')

    with st.expander("**重新合成**", expanded=True):
        with open(project_dir.replace("/pages", "/config") + '/font_data.txt', 'r', encoding='utf-8') as file:
            lines = file.readlines()
            fonts = [line.strip() for line in lines]
            font = st.selectbox('字幕字体：', fonts,
                                help="所有字体均从系统读取加载，支持用户自行安装字体。请注意商用风险！")
            col3, col4 = st.columns([0.9, 0.1], gap="medium")
            with col3:
                font_size = st.number_input('字体大小', min_value=1, max_value=30, value=18, step=1,
                                            help="推荐大小：18")
            with col4:
                font_color = st.color_picker('字体颜色', '#FFFFFF')

        if st.button("重新合成", type="primary", use_container_width=True):
            st.session_state.output2 = cache_dir + st.session_state.current
            with open(st.session_state.output2 + "/output.srt", 'w', encoding='utf-8') as srt_file:
                srt_file.write(st.session_state.srt_content_new)

            with st.spinner('正在合并视频，请耐心等待视频生成...'):
                srt_mv(st.session_state.output2, font, font_size, font_color)
with col2:
    with st.expander("**修改后的视频预览**", expanded=True):
        try:
            video_bytes = show_video(st.session_state.output2)
            st.video(video_bytes)
            result = time6 - time1
            formatted_result = f"{result:.2f}"
            st.success(f"合并成功！总用时：{formatted_result}秒")
            if st.button('查看文件目录', use_container_width=True):
                os.startfile(st.session_state.output2)
                st.warning("注意：文件夹已成功打开，可能未置顶显示，请检查任务栏！")
        except:
            st.warning('这里是第二次的视频预览窗口，运行后自动显示预览结果。')
