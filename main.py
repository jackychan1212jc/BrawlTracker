import os
import json
import requests
import pandas as pd
from datetime import datetime, timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from supabase import create_client, Client

app = FastAPI()

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").strip()
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "").strip()
BRAWL_API_TOKEN = os.environ.get("BRAWL_API_TOKEN", "").strip()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception:
    supabase = None

MODE_TRANSLATION = {
    'gemGrab': '寶石爭奪戰', 'brawlBall': '亂鬥足球', 'bounty': '搶星大作戰',
    'heist': '金庫攻防戰', 'hotZone': '據點搶奪戰', 'knockout': '極限淘汰賽',
    'wipeout': '積分爭奪戰', 'duels': '亂鬥擂台', 'soloShowdown': '單人生死鬥',
    'duoShowdown': '雙人生死鬥', 'basketBrawl': '亂鬥籃球', 'payload': '礦車競速',
    'lastStand': 'megaBoss', 'bossFight': '團隊首領戰', 'roboRumble': '機甲入侵', 
    'bigGame': '巨型獵場', 'brawlArena': '亂鬥競技場', 'arena': '亂鬥競技場'
}

PVE_MODES = ['lastStand', 'bossFight', 'roboRumble', 'bigGame', 'megaBoss']
TARGET_SIX_MODES = ['搶星大作戰', '寶石爭奪戰', '金庫攻防戰', '亂鬥足球', '據點搶奪戰', '極限淘汰賽']

startup_time_local = datetime.utcnow() + timedelta(hours=8)
current_local_time_str = startup_time_local.strftime('%Y-%m-%d %H:%M:%S')

def get_wr(w, l, d=0):
    total = w + l + d
    return f"{w/total*100:.1f}%" if total > 0 else "0.0%"

def fetch_and_save_data(target_tag: str):
    if not supabase or not BRAWL_API_TOKEN:
        return
    tag_formatted = target_tag.replace("#", "%23")
    url = f"https://bsproxy.royaleapi.dev/v1/players/{tag_formatted}/battlelog"
    headers = {"Authorization": f"Bearer {BRAWL_API_TOKEN}"}
    
    try:
        response = requests.get(url, headers=headers)
        if response.status_code != 200: return
        
        battles = response.json().get("items", [])
        for battle in battles:
            battle_time = battle.get("battleTime")
            if not battle_time: continue
            
            existing = supabase.table("battlelog").select("id").eq("battle_time", battle_time).eq("account", target_tag).execute()
            if len(existing.data) > 0:
                continue
                
            b = battle.get("battle") or {}
            event = battle.get("event") or {}
            
            my_brawler = "未知"
            brawler_trophies = "0"
            players_list = []
            
            if "teams" in b:
                for team in b["teams"]: players_list.extend(team)
            elif "players" in b:
                players_list = b["players"]
                
            for player in players_list:
                if player.get("tag") == target_tag:
                    b_data = player.get("brawler") or {}
                    my_brawler = b_data.get("name", "未知")
                    brawler_trophies = str(b_data.get("trophies", "0"))

            new_record = {
                "account": target_tag,
                "battle_time": battle_time,
                "mode": event.get("mode", "unknown"),
                "map": event.get("map", "unknown"),
                "type": b.get("type", "unknown"),
                "my_brawler": my_brawler,
                "brawler_trophies": brawler_trophies,
                "result": b.get("result", "draw"),
                "trophy_change": str(b.get("trophyChange", 0))
            }
            supabase.table("battlelog").insert(new_record).execute()
    except Exception:
        pass

def group_ranked_sets(df):
    if df.empty: return df
    df = df.copy()
    df['對戰時間_dt'] = pd.to_datetime(df['對戰時間'], errors='coerce')
    df = df.sort_values('對戰時間_dt', ascending=True).reset_index(drop=True)
    processed_rows = []
    current_set = []
    
    def flush_set(curr_set):
        if not curr_set: return []
        v_count = sum(1 for row in curr_set if row['戰果'] == 'victory')
        d_count = sum(1 for row in curr_set if row['戰果'] == 'defeat')
        if v_count >= 2 or d_count >= 2:
            rep_row = curr_set[-1].copy()
            rep_row['戰果'] = 'victory' if v_count >= 2 else 'defeat'
            return [rep_row]
        else: return []

    for idx, row in df.iterrows():
        is_ranked = row.get('類型') in ['soloRanked', 'teamRanked']
        mech = row.get('排位機制', 'BO1')
        if not is_ranked or mech != 'BO3':
            if current_set:
                processed_rows.extend(flush_set(current_set))
                current_set = []
            processed_rows.append(row)
            continue
        if not current_set: current_set.append(row)
        else:
            last_row = current_set[-1]
            same_type = row['類型'] == last_row['類型']
            same_map = row['地圖'] == last_row['地圖']
            same_brawler = row['我方英雄'] == last_row['我方英雄']
            time_diff = abs((row['對戰時間_dt'] - last_row['對戰時間_dt']).total_seconds()) / 60.0
            if same_type and same_map and same_brawler and time_diff <= 15:
                current_set.append(row)
                v_c = sum(1 for r in current_set if r['戰果'] == 'victory')
                d_c = sum(1 for r in current_set if r['戰果'] == 'defeat')
                if v_c >= 2 or d_c >= 2:
                    processed_rows.extend(flush_set(current_set))
                    current_set = []
            else:
                processed_rows.extend(flush_set(current_set))
                current_set = [row]
                
    if current_set: processed_rows.extend(flush_set(current_set))
    df_grouped = pd.DataFrame(processed_rows)
    if not df_grouped.empty:
        df_grouped = df_grouped.drop(columns=['對戰時間_dt'])
        df_grouped = df_grouped.sort_values('對戰時間', ascending=False)
    return df_grouped

def process_and_group_dataframe(df):
    if df.empty: return df
    df_valid = df[(df['戰果'].isin(['victory', 'defeat', 'draw'])) & (df['類型'] != 'friendly')].copy()
    df_grouped = group_ranked_sets(df_valid)
    if df_grouped.empty: return df_grouped
    
    def determine_classification(row):
        raw_type = str(row.get('類型', ''))
        raw_mode = str(row.get('模式', ''))
        base_mode_zh = MODE_TRANSLATION.get(raw_mode, raw_mode)
        
        if raw_type == 'challenge': ui_type = '挑戰'
        elif raw_mode in PVE_MODES: ui_type = '特別活動'
        elif raw_type in ['soloRanked', 'teamRanked']: ui_type = '排位賽'
        elif raw_type == 'ranked': ui_type = '一般模式'
        else: ui_type = '特別活動'
        return pd.Series([ui_type, base_mode_zh])
        
    df_grouped[['UI動態分類', '模式中文']] = df_grouped.apply(determine_classification, axis=1)
    return df_grouped

def build_ui_dict(df_grouped):
    ui_data = {'r_wins': 0, 'r_losses': 0, 'r_draws': 0, 't_wins': 0, 't_losses': 0, 't_draws': 0,
               'c_wins': 0, 'c_losses': 0, 'c_draws': 0, 's_wins': 0, 's_losses': 0, 's_draws': 0, 
               'brawler_stats': {}, 'map_stats': {}}
    if df_grouped is None or df_grouped.empty: return ui_data

    ui_data['r_wins'] = len(df_grouped[(df_grouped['UI動態分類'] == '排位賽') & (df_grouped['戰果'] == 'victory')])
    ui_data['r_losses'] = len(df_grouped[(df_grouped['UI動態分類'] == '排位賽') & (df_grouped['戰果'] == 'defeat')])
    ui_data['r_draws'] = len(df_grouped[(df_grouped['UI動態分類'] == '排位賽') & (df_grouped['戰果'] == 'draw')])
    ui_data['t_wins'] = len(df_grouped[(df_grouped['UI動態分類'] == '一般模式') & (df_grouped['戰果'] == 'victory')])
    ui_data['t_losses'] = len(df_grouped[(df_grouped['UI動態分類'] == '一般模式') & (df_grouped['戰果'] == 'defeat')])
    ui_data['t_draws'] = len(df_grouped[(df_grouped['UI動態分類'] == '一般模式') & (df_grouped['戰果'] == 'draw')])
    ui_data['c_wins'] = len(df_grouped[(df_grouped['UI動態分類'] == '挑戰') & (df_grouped['戰果'] == 'victory')])
    ui_data['c_losses'] = len(df_grouped[(df_grouped['UI動態分類'] == '挑戰') & (df_grouped['戰果'] == 'defeat')])
    ui_data['c_draws'] = len(df_grouped[(df_grouped['UI動態分類'] == '挑戰') & (df_grouped['戰果'] == 'draw')])
    ui_data['s_wins'] = len(df_grouped[(df_grouped['UI動態分類'] == '特別活動') & (df_grouped['戰果'] == 'victory')])
    ui_data['s_losses'] = len(df_grouped[(df_grouped['UI動態分類'] == '特別活動') & (df_grouped['戰果'] == 'defeat')])
    ui_data['s_draws'] = len(df_grouped[(df_grouped['UI動態分類'] == '特別活動') & (df_grouped['戰果'] == 'draw')])
    
    b_stats = {}
    m_stats = {}
    for _, row in df_grouped.iterrows():
        b = str(row['我方英雄']).upper()
        b_type = row['UI動態分類'] 
        b_mode = row['模式中文']
        res = row['戰果']
        
        if b not in ['NAN', 'NONE', '未知', '']:
            if b not in b_stats: b_stats[b] = {'W': 0, 'L': 0, 'D': 0, 'types': {}}
            if b_type not in b_stats[b]['types']: b_stats[b]['types'][b_type] = {'W': 0, 'L': 0, 'D': 0, 'modes': {}}
            if b_mode not in b_stats[b]['types'][b_type]['modes']: b_stats[b]['types'][b_type]['modes'][b_mode] = {'W': 0, 'L': 0, 'D': 0}
            if res == 'victory': 
                b_stats[b]['W'] += 1; b_stats[b]['types'][b_type]['W'] += 1; b_stats[b]['types'][b_type]['modes'][b_mode]['W'] += 1
            elif res == 'defeat': 
                b_stats[b]['L'] += 1; b_stats[b]['types'][b_type]['L'] += 1; b_stats[b]['types'][b_type]['modes'][b_mode]['L'] += 1
            elif res == 'draw': 
                b_stats[b]['D'] += 1; b_stats[b]['types'][b_type]['D'] += 1; b_stats[b]['types'][b_type]['modes'][b_mode]['D'] += 1
                
        if b_type not in m_stats: m_stats[b_type] = {'W': 0, 'L': 0, 'D': 0, 'modes': {}}
        if b_mode not in m_stats[b_type]['modes']: m_stats[b_type]['modes'][b_mode] = {'W': 0, 'L': 0, 'D': 0}
        if res == 'victory': m_stats[b_type]['W'] += 1; m_stats[b_type]['modes'][b_mode]['W'] += 1
        elif res == 'defeat': m_stats[b_type]['L'] += 1; m_stats[b_type]['modes'][b_mode]['L'] += 1
        elif res == 'draw': m_stats[b_type]['D'] += 1; m_stats[b_type]['modes'][b_mode]['D'] += 1
            
    ui_data['brawler_stats'] = b_stats
    ui_data['map_stats'] = m_stats
    return ui_data

def build_ranked_ui_dict(df_grouped):
    res = {}
    if df_grouped is None or df_grouped.empty: return res
    df_rk = df_grouped[df_grouped['UI動態分類'] == '排位賽'].copy()
    if df_rk.empty: return res
    
    if '賽季' not in df_rk.columns: df_rk['賽季'] = '未知賽季'
    
    for season, grp in df_rk.groupby('賽季'):
        s_season = str(season).strip()
        if s_season.endswith('.0'): s_season = s_season[:-2]
        if not s_season or s_season == 'nan': s_season = '未知賽季'
        
        start_date, end_date = "", ""
        if '對戰時間' in grp.columns:
            dates = grp['對戰時間'].dropna().astype(str).tolist()
            valid_dates = [d for d in dates if len(d) >= 10 and '-' in d[:10]]
            if valid_dates:
                valid_dates.sort()
                start_date = valid_dates[0][5:10].replace('-', '/')
                end_date = valid_dates[-1][5:10].replace('-', '/')
        
        s_w = len(grp[grp['戰果'] == 'victory'])
        s_l = len(grp[grp['戰果'] == 'defeat'])
        s_d = len(grp[grp['戰果'] == 'draw'])
        
        brawlers = {}
        for brawler, b_grp in grp.groupby('我方英雄'):
            b_w = len(b_grp[b_grp['戰果'] == 'victory'])
            b_l = len(b_grp[b_grp['戰果'] == 'defeat'])
            b_d = len(b_grp[b_grp['戰果'] == 'draw'])
            modes = {}
            for mode, m_grp in b_grp.groupby('模式中文'):
                m_w = len(m_grp[m_grp['戰果'] == 'victory'])
                m_l = len(m_grp[m_grp['戰果'] == 'defeat'])
                m_d = len(m_grp[m_grp['戰果'] == 'draw'])
                modes[mode] = {'w': m_w, 'l': m_l, 'd': m_d}
            brawlers[brawler] = {'w': b_w, 'l': b_l, 'd': b_d, 'modes': modes}
            
        res[s_season] = {
            'w': s_w, 'l': s_l, 'd': s_d, 
            'start_date': start_date, 'end_date': end_date, 
            'brawlers': brawlers
        }
    return res

def build_js_view_data(ui_data):
    r_wins, r_losses, r_draws = ui_data.get('r_wins', 0), ui_data.get('r_losses', 0), ui_data.get('r_draws', 0)
    t_wins, t_losses, t_draws = ui_data.get('t_wins', 0), ui_data.get('t_losses', 0), ui_data.get('t_draws', 0)
    c_wins, c_losses, c_draws = ui_data.get('c_wins', 0), ui_data.get('c_losses', 0), ui_data.get('c_draws', 0)
    s_wins, s_losses, s_draws = ui_data.get('s_wins', 0), ui_data.get('s_losses', 0), ui_data.get('s_draws', 0)
    brawler_stats = ui_data.get('brawler_stats', {})
    
    total_wins = r_wins + t_wins + c_wins + s_wins 
    total_losses = r_losses + t_losses + c_losses + s_losses 
    total_draws = r_draws + t_draws + c_draws + s_draws 
    
    merged_s_wins = s_wins + c_wins 
    merged_s_losses = s_losses + c_losses 
    merged_s_draws = s_draws + c_draws 
    
    summary = {
        'ranked': {'txt': f"{r_wins}W - {r_losses}L ({get_wr(r_wins, r_losses, r_draws)})", 'w': r_wins, 'l': r_losses, 'd': r_draws},
        'casual': {'txt': f"{t_wins}W - {t_losses}L ({get_wr(t_wins, t_losses, t_draws)})", 'w': t_wins, 'l': t_losses, 'd': t_draws},
        'special': {'txt': f"{merged_s_wins}W - {merged_s_losses}L ({get_wr(merged_s_wins, merged_s_losses, merged_s_draws)})", 'w': merged_s_wins, 'l': merged_s_losses, 'd': merged_s_draws},
        'total': {'txt': f"{total_wins}W - {total_losses}L ({get_wr(total_wins, total_losses, total_draws)})", 'w': total_wins, 'l': total_losses, 'd': total_draws}
    }
    
    brawlers = []
    for b_type_zh, icon in [('排位賽', '🏅'), ('一般模式', '⏳'), ('挑戰', '🎯'), ('特別活動', '🎪')]:
        type_brawlers = {}
        for b_name, b_data in brawler_stats.items():
            if b_type_zh in b_data.get('types', {}): type_brawlers[b_name] = b_data['types'][b_type_zh]
        if not type_brawlers: continue
        
        cat_dict = {'icon': icon, 'title': b_type_zh, 'items': []}
        sorted_brawlers = sorted(type_brawlers.items(), key=lambda item: (item[1]['W'] + item[1]['L'] + item[1]['D'], item[1]['W']), reverse=True)
        for b_name, b_stats_item in sorted_brawlers:
            w, l, d = b_stats_item['W'], b_stats_item['L'], b_stats_item['D']
            cat_dict['items'].append({'name': b_name.title(), 'stats': f"{w}W - {l}L ({get_wr(w, l, d)})", 'w': w, 'l': l, 'd': d})
        brawlers.append(cat_dict)
        
    brawler_details = {}
    for b_name, b_data in brawler_stats.items():
        tot_w, tot_l, tot_d = b_data['W'], b_data['L'], b_data['D']
        b_dict = {'summary': f"{tot_w}W - {tot_l}L ({get_wr(tot_w, tot_l, tot_d)})", 'w': tot_w, 'l': tot_l, 'd': tot_d, 'cats': []}
        for b_type_zh, icon in [('排位賽', '🏅'), ('一般模式', '⏳'), ('挑戰', '🎯'), ('特別活動', '🎪')]:
            if b_type_zh in b_data.get('types', {}):
                t_data = b_data['types'][b_type_zh]
                cat_js = {'icon': icon, 'title': b_type_zh, 'wins': t_data['W'], 'losses': t_data['L'], 'wr': get_wr(t_data['W'], t_data['L'], t_data['D']), 'w': t_data['W'], 'l': t_data['L'], 'd': t_data['D'], 'modes': []}
                sorted_modes = sorted(t_data['modes'].items(), key=lambda x: TARGET_SIX_MODES.index(x[0]) if x[0] in TARGET_SIX_MODES else 99)
                for m_zh, m_d in sorted_modes:
                    cat_js['modes'].append({'name': m_zh, 'stats': f"{m_d['W']}W - {m_d['L']}L ({get_wr(m_d['W'], m_d['L'], m_d['D'])})", 'w': m_d['W'], 'l': m_d['L'], 'd': m_d['D']})
                b_dict['cats'].append(cat_js)
        brawler_details[b_name] = b_dict

    js_map_stats = []
    for icon, cat in [('🏅', '排位賽'), ('⏳', '一般模式')]:
        modes = {}
        if cat == '一般模式':
            for m in TARGET_SIX_MODES:
                mw = ui_data.get('map_stats', {}).get('一般模式', {}).get('modes', {}).get(m, {}).get('W', 0)
                ml = ui_data.get('map_stats', {}).get('一般模式', {}).get('modes', {}).get(m, {}).get('L', 0)
                md = ui_data.get('map_stats', {}).get('一般模式', {}).get('modes', {}).get(m, {}).get('D', 0)
                modes[m] = {'W': mw, 'L': ml, 'D': md}
        else:
            cat_data = ui_data.get('map_stats', {}).get(cat, {})
            for m in TARGET_SIX_MODES:
                if m in cat_data.get('modes', {}):
                    modes[m] = cat_data['modes'][m]
                else:
                    modes[m] = {'W': 0, 'L': 0, 'D': 0}
                    
        w = sum(v['W'] for v in modes.values())
        l = sum(v['L'] for v in modes.values())
        d = sum(v['D'] for v in modes.values())
                
        def get_wr_internal(w,l,d): return f"{w/(w+l+d)*100:.1f}%" if w+l+d>0 else "0.0%"
        cat_js = {'icon': icon, 'title': cat, 'wins': w, 'losses': l, 'wr': get_wr_internal(w, l, d), 'w': w, 'l': l, 'd': d, 'modes': []}
        
        for m in TARGET_SIX_MODES:
            md = modes[m]
            cat_js['modes'].append({'name': m, 'stats': f"{md['W']}W - {md['L']}L ({get_wr_internal(md['W'], md['L'], md['D'])})", 'w': md['W'], 'l': md['L'], 'd': md['D']})
        js_map_stats.append(cat_js)

    return {'summary': summary, 'brawlers': brawlers, 'brawler_details': brawler_details, 'map_stats': js_map_stats}


@app.get("/")
def pro_dashboard(tag: str = ""):
    if not supabase:
        return HTMLResponse("<h1>資料庫連線失敗，請檢查環境變數。</h1>")

    tag = tag.strip().upper()
    if tag and not tag.startswith("#"):
        tag = "#" + tag

    dashboard_display_nav = "grid" if tag else "none"
    dashboard_display = "block" if tag else "none"
    dashboard_display_search = "flex" if tag else "none"
    welcome_display = "block" if not tag else "none"
    refresh_status_text = "等待玩家輸入標籤"
    
    empty_view = {'summary': {'ranked': {'txt': "0W - 0L (0.0%)", 'w': 0, 'l': 0, 'd': 0}, 'casual': {'txt': "0W - 0L (0.0%)", 'w': 0, 'l': 0, 'd': 0}, 'special': {'txt': "0W - 0L (0.0%)", 'w': 0, 'l': 0, 'd': 0}, 'total': {'txt': "0W - 0L (0.0%)", 'w': 0, 'l': 0, 'd': 0}}, 'brawlers': [], 'brawler_details': {}, 'map_stats': []}
    
    current_trophies = 0
    victories_3v3 = 0
    elo_val = 0
    tier = "UNKNOWN"
    player_name = ""
    ui_all_time = empty_view
    ui_session = empty_view
    ranked_seasons_all_time = {}
    ranked_seasons_session = {}

    if tag:
        refresh_status_text = "資料庫同步完成"
        fetch_and_save_data(tag)

        tag_formatted = tag.replace("#", "%23")
        url = f"https://bsproxy.royaleapi.dev/v1/players/{tag_formatted}"
        headers = {"Authorization": f"Bearer {BRAWL_API_TOKEN}"}
        
        current_season_id = 48
        try:
            res = requests.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                player_name = data.get("name", "")
                current_trophies = data.get("trophies", 0)
                victories_3v3 = data.get("3vs3Victories", 0)
                elo_val = data.get("rankedElo", 0)
                tier = data.get("rankedRankName", "UNKNOWN")
                current_season_id = data.get('rankedSeasonId', 48)
