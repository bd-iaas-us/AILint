import pandas as pd
import streamlit as st
import metrics
import argparse
from streamlit_option_menu import option_menu

has_parse_se_logs = False
try:
    import parse_se_logs
    has_parse_se_logs = True
except:
    import metrics

def rag():
    tab1, tab2 = st.tabs(["Upload files", "List files"])
    with tab1:
        st.markdown("<span style='color: red'>only utf-8 text file could be accepted</span>", unsafe_allow_html=True)
        uploaded_files = st.file_uploader("Choose your files", accept_multiple_files=True)
        if uploaded_files:
            for file in uploaded_files:
                content = file.getvalue().decode("utf-8")
                print(content)
            st.success(f"Uploaded {len(uploaded_files)} files successfully!")
    with tab2:
        #search tab
        st.markdown("TODO")
        
def metrics(traj_dir :str, lint_file: str):
    if has_parse_se_logs:
        lint_points = parse_se_logs.parse_lint_log(lint_file)
        traj_points = parse_se_logs.parse_traj_log(traj_dir)
        df_traj = pd.DataFrame(traj_points)
        df_lint = pd.DataFrame(lint_points)
    else:
        traj_points = metrics.parse_swe_traj(traj_dir)
        df_traj = pd.DataFrame([tp.model_dump() for tp in traj_points])
        lint_points = metrics.parse_lint(lint_file)
        df_lint = pd.DataFrame([{"time":lp.time, "dur": lp.dur} for lp in lint_points])



    st.write(f'### total dev requests {len(traj_points)}')
    st.write("### Open API Calls Over Time")
    st.bar_chart(df_traj[['time', 'api_calls']], x="time")

    st.write("### Open API tokens send/received Over Time")
    st.bar_chart(df_traj[['time', 'tokens_sent', 'tokens_received']], x="time")

    st.write("### Open API Calls status")
    st.bar_chart(df_traj[['time', 'exit_status']], x="time")


    #only time.
    st.write("### when lint is invoked")
    st.scatter_chart(df_lint, x='time')

#example: streamlit run ui.py lint.log .
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse log files")
    parser.add_argument('lint_log_file', type=str, help='filename of log file')
    parser.add_argument("traj_dir", type=str, help="diretory of traj")
    args = parser.parse_args()

    st.set_page_config(
    page_title="autose",
    page_icon="🐼", 
    layout="wide",
    initial_sidebar_state="expanded" 
)
    st.sidebar.title("Navigation")
    metrics_title = "Metrics"
    rag_title = "RAG File management"
    with st.sidebar:
        app_mode = option_menu("Dashboard", [metrics_title, rag_title], 
                        icons=['bar-chart-fill', 'cloud-upload'], 
                        menu_icon="cast", default_index=0, 
                        orientation="vertical", 
                        styles={
                            "container": {"padding": "0!important", "background-color": "#fafafa"},
                            "icon": {"color": "orange", "font-size": "25px"},
                            "nav-link": {"font-size": "16px", "text-align": "left", "margin":"0px", "--hover-color": "#eee"},
                            "nav-link-selected": {"background-color": "#02ab21"},
                        })
    #app_mode = st.sidebar.radio("Choose the view", ["Metrics", "Upload File"])
    if app_mode == metrics_title:
        metrics(args.traj_dir, args.lint_log_file)
    elif app_mode == rag_title:
        rag()