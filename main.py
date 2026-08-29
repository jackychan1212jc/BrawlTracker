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

    # 完全匿名化處理，無任何硬編碼標籤
    tag = tag.strip().upper()
    if tag and not tag.startswith("#"):
        tag = "#" + tag

    # UI 顯示狀態切換
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
        except Exception:
            pass
            
        res = supabase.table("battlelog").select("*").eq("account", tag).order("battle_time", desc=True).execute()
        df = pd.DataFrame(res.data)
        
        if not df.empty:
            df = df.rename(columns={
                'battle_time': '對戰時間',
                'mode': '模式',
                'map': '地圖',
                'type': '類型',
                'my_brawler': '我方英雄',
                'result': '戰果',
                'brawler_trophies': '英雄盃數'
            })
            
            def check_bo3(row):
                if row.get('類型') in ['soloRanked', 'teamRanked']:
                    try: return 'BO3' if int(row.get('英雄盃數', 0)) >= 13 else 'BO1'
                    except: return 'BO3'
                return '一般'
            
            def determine_season(r):
                if r.get('類型') not in ['soloRanked', 'teamRanked']: return ''
                dt_str = str(r.get('對戰時間', ''))
                try:
                    if datetime.strptime(dt_str, '%Y%m%dT%H%M%S.000Z') <= datetime(2026, 8, 19, 23, 59, 59): 
                        return '47'
                    return str(current_season_id)
                except: return '48'

            df['排位機制'] = df.apply(check_bo3, axis=1)
            df['賽季'] = df.apply(determine_season, axis=1)
            
            df_all_time_grouped = process_and_group_dataframe(df)
            ui_all_time = build_ui_dict(df_all_time_grouped)
            ranked_seasons_all_time = build_ranked_ui_dict(df_all_time_grouped)
            
            df_session = df[df['對戰時間'] > current_local_time_str.replace('-', '').replace(' ', 'T').replace(':', '') + '.000Z'].copy()
            df_session_grouped = process_and_group_dataframe(df_session)
            ui_session = build_ui_dict(df_session_grouped)
            ranked_seasons_session = build_ranked_ui_dict(df_session_grouped)

    app_data = {
        "current_player": {
            'name': player_name,
            'color': "#00FFAA", 'trophies': current_trophies, 'diff_trophies': '+0', 
            'victories_3v3': victories_3v3, 'elo': str(elo_val), 'diff_elo': '+0', 'tier': tier,
            'session': build_js_view_data(ui_session) if tag else empty_view, 
            'all_time': build_js_view_data(ui_all_time) if tag else empty_view,
            'ranked_seasons_session': ranked_seasons_session, 
            'ranked_seasons_all_time': ranked_seasons_all_time
        }
    }
    
    js_string = json.dumps(app_data, ensure_ascii=False)
    
    html_template = """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>Brawl Tracker</title>
        <style>
            :root { --theme-color: #00FFAA; }
            
            body { padding: 100px 8vw 40px 8vw; margin: 0; display: flex; justify-content: center; overflow-y: scroll; background-color: #121212; color: #FFFFFF; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; }
            body.no-scroll { overflow: hidden; }

            .container { width: 100%; max-width: 900px; background-color: #1A1F24; border-radius: 15px; border: 1px solid #2A323C; padding: 40px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); position: relative; }
            
            .nav-bar { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; margin-bottom: 25px; gap: 15px; }
            .nav-group { display: flex; gap: 10px; background-color: #121212; padding: 5px; border-radius: 10px; border: 1px solid #2A323C; }
            .nav-btn { background: none; border: none; color: #555555; font-size: 16px; font-weight: bold; cursor: pointer; padding: 8px 20px; border-radius: 6px; transition: all 0.3s; font-family: 'Consolas', monospace; }
            .nav-btn:hover { background-color: #2A323C; color: #FFFFFF !important; }
            .nav-btn.active { background-color: #2A323C; }
            
            .top-acc-btn { background: transparent; border: none; border-right: 1px solid #2A323C; color: #AAAAAA; padding: 0 15px; font-weight: bold; cursor: pointer; font-size: 14px; transition: 0.2s; font-family: 'Consolas', monospace; height: 100%; pointer-events: auto; }
            .top-acc-btn:last-child { border-right: none; }
            .top-acc-btn:hover:not(.active) { background: #2A323C; color: #FFFFFF; }
            .top-acc-btn.active { background: var(--theme-color); color: #121212; }

            .yt-link {
                display: flex;
                align-items: center;
                gap: 8px;
                background-color: #1A1F24;
                border: 1px solid #2A323C;
                padding: 6px 14px;
                border-radius: 20px;
                color: #DDDDDD;
                text-decoration: none;
                font-family: 'Segoe UI', Tahoma, sans-serif;
                font-weight: bold;
                font-size: 14px;
                transition: all 0.3s ease;
                pointer-events: auto;
            }
            .yt-link:hover {
                background-color: #2A323C;
                color: #FFFFFF;
                border-color: #FF0000;
                box-shadow: 0 0 12px rgba(255, 0, 0, 0.4);
                transform: translateY(-1px);
            }

            .search-box { display: flex; gap: 10px; }
            .search-box input { background-color: #121212; border: 1px solid #2A323C; color: var(--theme-color); border-radius: 8px; font-family: 'Consolas', monospace; font-size: 16px; outline: none; transition: border-color 0.3s; box-sizing: border-box; }
            .search-box input:focus { border-color: var(--theme-color); }
            .search-box button { background-color: #1A1F24; border: 1px solid #2A323C; color: #FFFFFF; border-radius: 8px; cursor: pointer; transition: all 0.3s; font-family: 'Consolas', monospace; font-weight: bold; box-sizing: border-box; }
            .search-box button:hover { background-color: var(--theme-color); color: #121212; }

            .header { display: flex; justify-content: space-between; align-items: flex-start; border-bottom: 3px solid var(--theme-color); padding-bottom: 20px; margin-bottom: 30px; transition: border-color 0.3s; flex-wrap: nowrap; overflow: hidden; }
            
            .top-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }
            .stat-box { background-color: #121212; border-radius: 12px; padding: 20px; text-align: center; border-left: 4px solid var(--theme-color); transition: border-color 0.3s; }
            .stat-box .title { font-size: 16px; color: #AAAAAA; margin-bottom: 8px; font-weight: bold; white-space: nowrap; }
            .stat-box .value { font-size: 24px; font-weight: bold; color: #FFFFFF; font-family: 'Consolas', monospace; transition: text-shadow 0.3s, color 0.3s; }
            .stat-box .diff { font-size: 14px; color: var(--theme-color); transition: color 0.3s; }
            
            .summary-section { background-color: #121212; border-radius: 12px; padding: 25px; margin-bottom: 40px; }
            
            .brawler-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 25px; }
            .brawler-cat { background-color: #121212; border-radius: 12px; padding: 20px; border: 1px solid #1A1F24; transition: all 0.3s ease; }
            .brawler-cat:hover { border-color: var(--theme-color); transform: translateY(-5px); }
            .brawler-cat h3 { margin: 0 0 15px 0; color: var(--theme-color); font-size: 20px; border-bottom: 2px solid #2A323C; padding-bottom: 12px; transition: color 0.3s; }
            
            .footer { text-align: center; margin-top: 30px; color: #555555; font-size: 14px; }

            .b-line { display: flex; justify-content: space-between; padding: 6px 0; font-family: 'Consolas', monospace; font-size: 16px; border-bottom: 1px solid #1A1F24; }
            .b-line:last-child { border-bottom: none; }
            .b-name { color: #DDDDDD; font-weight: bold; }
            .b-data { color: #FFFFFF; }
            
            .b-line-bar { padding: 6px 0; border-bottom: 1px solid #1A1F24; }
            .b-line-bar:last-child { border-bottom: none; }
            .b-line-bar .bar-label { display: flex; justify-content: space-between; margin-bottom: 5px; font-family: 'Consolas', monospace; font-size: 15px; }
            .b-line-bar .bar-track { display: flex; width: 100%; height: 6px; background-color: #2A323C; border-radius: 3px; overflow: hidden; }
            
            .summary-line { display: flex; justify-content: space-between; padding: 12px 0; border-bottom: 1px dashed #2A323C; font-size: 20px; font-family: 'Consolas', monospace; }
            .summary-line.is-total { border-bottom: none; font-weight: bold; font-size: 24px; color: var(--theme-color); margin-top: 10px; transition: color 0.3s; }
            
            .summary-line-bar { padding: 12px 0; border-bottom: 1px dashed #2A323C; }
            .summary-line-bar.is-total { border-bottom: none; margin-top: 10px; }
            .summary-line-bar .bar-label { display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 20px; font-family: 'Consolas', monospace; }
            .summary-line-bar.is-total .bar-label { font-weight: bold; font-size: 24px; color: var(--theme-color); }
            .summary-line-bar .bar-track { display: flex; width: 100%; height: 8px; background-color: #2A323C; border-radius: 4px; overflow: hidden; }

            .bar-fill { height: 100%; transition: width 0.5s ease; }
            .bar-fill.win { background-color: var(--theme-color); }

            ::-webkit-scrollbar { width: 8px; }
            ::-webkit-scrollbar-track { background: transparent; }
            ::-webkit-scrollbar-thumb { background: rgba(255, 255, 255, 0.15); border-radius: 10px; border: 2px solid #1A1F24; }
            ::-webkit-scrollbar-thumb:hover { background: rgba(255, 255, 255, 0.3); }

            .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(0,0,0,0.85); backdrop-filter: blur(5px); justify-content: center; align-items: center; }
            .modal-content { max-height: 95vh; overflow-y: auto; background-color: #1A1F24; width: 95%; max-width: 500px; border-radius: 15px; border: 1px solid #2A323C; box-shadow: 0 10px 40px rgba(0,0,0,0.8); transition: max-width 0.3s ease; }
            .modal-header { background: linear-gradient(135deg, #1A1F24 0%, #2A323C 100%); padding: 15px 25px; display: flex; justify-content: space-between; align-items: center; border-bottom: 3px solid var(--theme-color); position: sticky; top: 0; z-index: 10; }
            .modal-header h1 { margin: 0; color: #FFFFFF; font-size: 22px; font-family: 'Consolas', monospace; }
            .close-btn { color: #AAAAAA; font-size: 32px; font-weight: bold; cursor: pointer; transition: color 0.3s; line-height: 1; }
            .close-btn:hover { color: var(--theme-color); }
            .modal-body { padding: 15px 20px; }
            .modal-body .brawler-cat { margin-bottom: 12px; padding: 12px; }

            .map-view-grid { display: flex; flex-direction: column; gap: 12px; }
            .map-view-grid .brawler-cat { padding: 15px 25px; margin-bottom: 0; }
            .map-view-grid .brawler-cat h3 { margin: 0 0 5px 0; font-size: 20px; padding-bottom: 8px; }
            .map-view-grid .summary-line { padding: 8px 0; font-size: 18px; border-bottom: 1px solid #2A323C; margin-bottom: 6px; }
            .map-view-grid .b-line { padding: 5px 0; font-size: 16px; border-bottom: 1px dashed #1A1F24; }
            .map-view-grid .b-line:last-child { border-bottom: none; }
            
            .page-container { display: none; animation: fadeIn 0.3s ease-in-out; }
            .page-container.active { display: block; }
            @keyframes fadeIn { from { opacity: 0; transform: translateY(5px); } to { opacity: 1; transform: translateY(0); } }
        </style>
    </head>
    <body>
        
        <div style="width: 100%; background-color: #0B1015; border-bottom: 2px solid #1A1F24; height: 64px; position: fixed; top: 0; left: 0; z-index: 1000; box-shadow: 0 4px 20px rgba(0,0,0,0.6); box-sizing: border-box;">
            
            <div style="position: absolute; left: 5vw; top: 0; bottom: 0; display: flex; align-items: center; z-index: 10; pointer-events: auto;">
                <a href="javascript:window.location.reload();" style="display: flex; align-items: center; gap: 10px; text-decoration: none; cursor: pointer;" title="重新整理資料">
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="36" height="36">
                        <rect x="2" y="8" width="28" height="16" rx="8" fill="#00FFAA" />
                        <path d="M 8 15 h 2 v -2 h 2 v 2 h 2 v 2 h -2 v 2 h -2 v -2 h -2 z" fill="#0B1015" />
                        <circle cx="23" cy="14" r="1.8" fill="#0B1015" />
                        <circle cx="19.5" cy="17.5" r="1.8" fill="#0B1015" />
                    </svg>
                    <span style="background: linear-gradient(90deg, #00FFAA, #00b3ff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; font-size: 26px; font-weight: 900; letter-spacing: 1.5px; font-family: 'Segoe UI', Tahoma, sans-serif;">
                        Brawl Tracker
                    </span>
                </a>
            </div>
            
            <!-- ✨ 完美對齊：透過 CSS 算式讓 YT 剛好貼齊下方黑底的左側 -->
            <div style="position: absolute; left: max(5vw, calc(50vw - 450px)); top: 0; bottom: 0; display: flex; align-items: center; pointer-events: none; z-index: 5;">
                <a href="http://www.youtube.com/@Jacky%E9%99%B3%E7%9A%AE" target="_blank" class="yt-link" title="前往 Jacky陳皮 的 YouTube 頻道" style="margin-left: 40px;"> <!-- 40px 剛好抵銷下方 container 的 padding -->
                    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="18" height="18" fill="#FF0000">
                        <path d="M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z"/>
                    </svg>
                    <span>YT: Jacky陳皮</span>
                </a>
            </div>

            <!-- ✨ 完美對齊：透過 CSS 算式讓帳號區塊剛好貼齊下方黑底的右側 -->
            <div style="position: absolute; right: max(5vw, calc(50vw - 450px)); top: 0; bottom: 0; display: flex; align-items: center; gap: 20px; z-index: 10; pointer-events: auto; padding-right: 40px;">
                <div id="top-acc-container" style="display: flex; background: #121212; border: 1px solid #2A323C; border-radius: 8px; overflow: hidden; height: 36px;">
                    <!-- JS 動態生成 -->
                </div>

                <div class="lang-switch" style="display: flex; background: #121212; border: 1px solid #2A323C; border-radius: 8px; overflow: hidden; height: 36px; pointer-events: auto;">
                    <button id="lang-zh" onclick="setLang('zh')" style="background: var(--theme-color); color: #121212; border: none; padding: 0 15px; font-weight: bold; cursor: pointer; font-size: 15px; transition: 0.2s;">繁</button>
                    <button id="lang-en" onclick="setLang('en')" style="background: transparent; color: #AAAAAA; border: none; padding: 0 15px; font-weight: bold; cursor: pointer; font-size: 15px; transition: 0.2s;">EN</button>
                </div>
            </div>
        </div>

        <div class="container">
            
            <div class="nav-bar" style="display: __DASHBOARD_DISPLAY_NAV__;">
                <div class="nav-group" style="justify-self: flex-start;">
                    <button id="btn-page-toggle" class="nav-btn active" style="color: var(--theme-color); width: 170px; text-align: center; white-space: nowrap;" onclick="togglePage()">▶ 切換至排位賽</button>
                </div>
                
                <div style="display: flex; gap: 15px; justify-self: center;">
                    <div class="nav-group" id="display-nav">
                        <button class="nav-btn" onclick="setDisplayMode('data')" id="btn-disp-data" title="文字數據版">🔢</button>
                        <button class="nav-btn" onclick="setDisplayMode('bar')" id="btn-disp-bar" title="進度條狀版">📊</button>
                    </div>
                    <div class="nav-group" id="align-nav">
                        <button class="nav-btn" onclick="setAlignment('flex-start')" id="btn-align-left" title="靠左對齊">⬅️</button>
                        <button class="nav-btn" onclick="setAlignment('center')" id="btn-align-center" title="置中對齊">⏹️</button>
                        <button class="nav-btn" onclick="setAlignment('flex-end')" id="btn-align-right" title="靠右對齊">➡️</button>
                    </div>
                </div>
                
                <div class="nav-group" id="view-nav" style="justify-self: flex-end;">
                    <button class="nav-btn" onclick="switchView('session')" id="btn-session" style="width: 140px; text-align: center;">▶ 本次區間</button>
                    <button class="nav-btn" onclick="switchView('all_time')" id="btn-all_time" style="width: 140px; text-align: center;">▶ 歷史總計</button>
                </div>
            </div>

            <div class="header">
                <div style="flex: 1; display: flex; flex-direction: column; justify-content: flex-start; align-items: flex-start;">
                    
                    <form id="track-form" onsubmit="handleTrackSubmit(event)" style="display:flex; align-items:center; gap: 10px; margin:0;">
                        <span id="lbl-tag" style="color:var(--theme-color); font-size:20px; font-weight:bold; white-space:nowrap; text-shadow: 0 0 10px rgba(0,255,170,0.3); display: inline-block;">請輸入玩家標籤：</span>
                        <input type="text" id="input-tag" value="__CURRENT_TAG__" placeholder="#XXXXXXX" required style="background-color:#121212; border:2px solid #2A323C; color:white; padding:8px 12px; border-radius:8px; font-family:'Consolas', monospace; font-size:18px; outline:none; text-transform:uppercase; width:140px; transition: border-color 0.3s;" onfocus="this.style.borderColor='var(--theme-color)'" onblur="this.style.borderColor='#2A323C'">
                        <button type="submit" id="btn-track" style="background-color:var(--theme-color); color:#121212; font-weight:bold; font-size:16px; padding:8px 0; width: 80px; text-align: center; border-radius:8px; border:none; cursor:pointer; transition: opacity 0.3s; white-space:nowrap;" onmouseover="this.style.opacity='0.8'" onmouseout="this.style.opacity='1'">追蹤</button>
                    </form>

                    <div id="player-name-display" style="display:none; align-items:center; gap: 15px; margin:0;">
                        <span id="lbl-current-player" style="color:var(--theme-color); font-size:20px; font-weight:bold; white-space:nowrap; text-shadow: 0 0 10px rgba(0,255,170,0.3); display: inline-block;">當前玩家：</span>
                        <span id="val-player-name" style="color:#FFFFFF; font-size:26px; font-weight:900; letter-spacing:1px; white-space:nowrap;">-</span>
                        <button type="button" id="btn-reenter" onclick="showInputForm()" style="background-color:transparent; border:1px solid #2A323C; color:#AAAAAA; font-weight:bold; font-size:14px; padding:6px 15px; border-radius:8px; cursor:pointer; transition: all 0.3s; white-space:nowrap;" onmouseover="this.style.color='#FFF'; this.style.borderColor='var(--theme-color)';" onmouseout="this.style.color='#AAAAAA'; this.style.borderColor='#2A323C';">重新輸入</button>
                    </div>

                </div>
                
                <div style="display: __DASHBOARD_DISPLAY_SEARCH__; align-items: center;">
                    <div class="search-box" style="margin-bottom: 0;">
                        <input type="text" id="searchInput" placeholder="🔍 搜尋英雄、地圖" onkeypress="if(event.key === 'Enter') handleSearch()" style="width: 240px; padding: 8px 12px; box-sizing: border-box;">
                        <button id="btn-search" onclick="handleSearch()" style="padding: 8px 0; width: 80px; text-align: center; white-space:nowrap; box-sizing: border-box;">查詢</button>
                    </div>
                </div>
            </div>

            <div style="display: __WELCOME_DISPLAY__; text-align: center; padding: 80px 20px;">
                <div style="font-size: 60px; margin-bottom: 20px;">🎮</div>
                <h2 id="welcome-title" style="color:var(--theme-color); font-size:32px; margin-bottom:15px; letter-spacing: 2px;">歡迎使用戰術主控台</h2>
                <p id="welcome-desc" style="color:#AAAAAA; font-size:18px; line-height: 1.6;">這是一套強大的 Brawl Stars 電競級數據分析系統。<br>請在上方輸入玩家標籤 (包含 #) 以建立或查看該玩家的專屬戰報。</p>
            </div>

            <div id="dashboard-wrapper" style="display: __DASHBOARD_DISPLAY__;">
                <div id="page-main" class="page-container active">
                    <div class="top-stats">
                        <div class="stat-box"><div class="title" id="title-trophies">🏆 總盃數</div><div class="value" id="val-trophies">- <span class="diff" id="diff-trophies">(-)</span></div></div>
                        <div class="stat-box"><div class="title" id="title-3v3">⚔️ 3V3 勝場</div><div class="value" id="val-3v3">-</div></div>
                        <div class="stat-box"><div class="title" id="title-elo">🎯 排位 Elo</div><div class="value" id="val-elo">- <span class="diff" id="diff-elo">(-)</span></div></div>
                        <div class="stat-box"><div class="title" id="title-tier">⭐ 排位段位</div><div class="value" id="val-tier">-</div></div>
                    </div>
                    
                    <div class="summary-section" id="summary-section"></div>
                    <div class="brawler-grid" id="brawler-grid"></div>
                </div>

                <div id="page-ranked" class="page-container">
                    <div style="display: grid; grid-template-columns: repeat(2, 1fr); gap: 20px; margin-bottom: 30px;">
                        <div class="stat-box enlarged" style="display: flex; flex-direction: column; justify-content: center; padding: 30px;"><div class="title" id="title-elo-rk">🎯 排位 Elo</div><div class="value" id="val-elo-rk">- <span class="diff" id="diff-elo-rk">(-)</span></div></div>
                        <div class="stat-box enlarged" style="display: flex; flex-direction: column; justify-content: center; padding: 30px;"><div class="title" id="title-tier-rk">⭐ 排位段位</div><div class="value" id="val-tier-rk">-</div></div>
                    </div>
                    
                    <div class="summary-section" id="summary-ranked-only" style="margin-bottom: 40px; padding: 15px 25px;"></div>
                    
                    <div id="ranked-seasons-container"></div>
                </div>
            </div>

            <div class="footer">
                <span id="footer-cloud">系統運作於 Render 雲端環境</span> <br>
                <span id="refresh-status" style="color:var(--theme-color);">__REFRESH_TEXT__</span>
                <div style="margin-top: 15px;">
                    <a href="javascript:void(0);" onclick="localStorage.clear(); sessionStorage.clear(); window.location.href='/';" id="btn-clear-cache" style="color: #555; text-decoration: none; font-size: 12px; transition: color 0.3s;" onmouseover="this.style.color='#FF5555'" onmouseout="this.style.color='#555'">[ 🗑️ 清除本機綁定紀錄 ]</a>
                </div>
            </div>
        </div>

        <div id="searchModal" class="modal">
            <div class="modal-content" id="modal-content-box">
                <div class="modal-header">
                    <h1 id="modal-title">戰術透視</h1>
                    <span class="close-btn" onclick="closeModal()">&times;</span>
                </div>
                <div class="modal-body" id="modal-body"></div>
            </div>
        </div>

        <script>
            let appData = __APP_DATA_HERE__;
            window.appData = appData;
            
            let currentView = sessionStorage.getItem('currentView') || "all_time";
            let currentAlign = localStorage.getItem('pageAlign') || 'center';
            let currentDisplayMode = localStorage.getItem('displayMode') || 'bar'; 
            let activePage = sessionStorage.getItem('activePage') || 'main';
            let currentLang = localStorage.getItem('lang') || 'zh';
            
            const urlParams = new URLSearchParams(window.location.search);
            let currentUrlTag = urlParams.get('tag');
            if (currentUrlTag) {
                currentUrlTag = currentUrlTag.toUpperCase().replace('%23', '#');
                if (!currentUrlTag.startsWith('#')) currentUrlTag = '#' + currentUrlTag;
            } else {
                currentUrlTag = null;
            }
            
            const TARGET_SIX_MODES = ['搶星大作戰', '寶石爭奪戰', '金庫攻防戰', '亂鬥足球', '據點搶奪戰', '極限淘汰賽'];

            const i18n = {
                'zh': {
                    tag_lbl: '請輸入玩家標籤：', track: '追蹤', search_ph: '🔍 搜尋英雄、地圖', search_btn: '查詢',
                    welcome_t: '歡迎使用戰術主控台',
                    welcome_d: '這是一套強大的 Brawl Stars 電競級數據分析系統。<br>請在上方輸入玩家標籤 (包含 #) 以建立或查看該玩家的專屬戰報。',
                    trophies: '🏆 總盃數', v3v3: '⚔️ 3V3 勝場', elo: '🎯 排位 Elo', tier: '⭐ 排位段位',
                    footer: '系統運作於 Render 雲端環境',
                    btn_ranked: '▶ 切換至排位賽', btn_main: '◀ 返回總戰績',
                    btn_ses: '▶ 本次區間', btn_all: '▶ 歷史總計',
                    m_bounty: '搶星大作戰', m_gem: '寶石爭奪戰', m_heist: '金庫攻防戰', m_ball: '亂鬥足球', m_hot: '據點搶奪戰', m_knock: '極限淘汰賽',
                    c_rk: '🏅 排位賽', c_ca: '⏳ 一般模式', c_sp: '🎪 特別活動', c_tot: '📊 總戰績',
                    rk_ses: '🏅 排位戰績 (本次)', rk_all: '🏅 排位總計 (歷史)',
                    no_rk_ses: '本次區間尚未進行任何排位賽', no_rk_all: '資料庫中尚無排位賽紀錄',
                    season: ' 第 {s} 賽季', ses_match: '本次對戰', total_match: '總局數: ',
                    no_hero: '(本次未出戰)', hero_ses: '⚔️ 本次出戰英雄 ({n}場)',
                    req_3: '(該模式需出場滿 3 次才能計算排行榜)', pr_top: '📊 出場率 Top 3', wr_top: '🏆 勝率 Top 3',
                    trap: '⚠️ 版本陷阱 (頭鐵掉分機)', gem: '💎 潛力神角 (上分奇兵)', wr: '勝率',
                    modal_tot: '【 全模式地圖勝率 (歷史總計) 】', modal_not_found: '資料庫中找不到包含【{q}】的英雄紀錄。',
                    cat_tot: '分類總計', sum_wl: '總勝負', pr: '出場率',
                    acc1: 'Main', acc2: 'Alt 1', acc3: 'Alt 2',
                    clear_cache: '[ 🗑️ 清除本機綁定紀錄 ]',
                    current_player_lbl: '當前玩家：', btn_reenter: '重新輸入'
                },
                'en': {
                    tag_lbl: 'Player Tag:', track: 'Track', search_ph: '🔍 Search Brawler / Map', search_btn: 'Search',
                    welcome_t: 'Welcome to Brawl Tactics',
                    welcome_d: 'A powerful esports-grade data analytics system for Brawl Stars.<br>Enter a player tag (including #) above to track or view stats.',
                    trophies: '🏆 Trophies', v3v3: '⚔️ 3V3 Wins', elo: '🎯 Ranked Elo', tier: '⭐ Ranked Tier',
                    footer: 'Powered by Render Cloud Environment',
                    btn_ranked: '▶ Ranked Mode', btn_main: '◀ Total Stats',
                    btn_ses: '▶ Session', btn_all: '▶ All-Time',
                    m_bounty: 'Bounty', m_gem: 'Gem Grab', m_heist: 'Heist', m_ball: 'Brawl Ball', m_hot: 'Hot Zone', m_knock: 'Knockout',
                    c_rk: '🏅 Ranked', c_ca: '⏳ Casual', c_sp: '🎪 Special', c_tot: '📊 Total',
                    rk_ses: '🏅 Ranked (Session)', rk_all: '🏅 Ranked (All-Time)',
                    no_rk_ses: 'No ranked matches played in this session', no_rk_all: 'No ranked records in database',
                    season: ' Season {s}', ses_match: 'Session Match', total_match: 'Total Matches: ',
                    no_hero: '(No matches in session)', hero_ses: '⚔️ Heroes Played ({n})',
                    req_3: '(Requires 3 matches to rank)', pr_top: '📊 Pick Rate Top 3', wr_top: '🏆 Win Rate Top 3',
                    trap: '⚠️ Meta Trap (Trophy Drain)', gem: '💎 Hidden Gem (Trophy Pusher)', wr: 'Win Rate',
                    modal_tot: '【 Win Rate by Mode/Map (All-Time) 】', modal_not_found: 'No records found for brawler containing "{q}".',
                    cat_tot: 'Category Total', sum_wl: 'Total W/L', pr: 'Pick Rate',
                    acc1: 'Main', acc2: 'Alt 1', acc3: 'Alt 2',
                    clear_cache: '[ 🗑️ Clear Local Data ]',
                    current_player_lbl: 'Current Player:', btn_reenter: 'Change Tag'
                }
            };

            // ✨ 直覺化綁定：點擊任何按鈕，自動綁定當前畫面戰績
            function handleAccClick(slot) {
                let savedTag = localStorage.getItem('acc' + slot);
                if (savedTag) {
                    // 已綁定，切換帳號
                    if (currentUrlTag !== savedTag) {
                        window.location.href = '/?tag=' + encodeURIComponent(savedTag);
                    }
                } else {
                    // 未綁定
                    if (currentUrlTag) {
                        // 正在看戰績，直接綁定給它！
                        localStorage.setItem('acc' + slot, currentUrlTag);
                        
                        let btn = document.getElementById('btn-acc-' + slot);
                        if(btn) {
                            let originalText = btn.innerText;
                            btn.innerText = '✔️';
                            btn.style.color = 'var(--theme-color)';
                            setTimeout(() => { renderTopAccButtons(); }, 1000);
                        }
                    } else {
                        // 如果在首頁亂點空按鈕，閃爍輸入框提醒他
                        let inputEl = document.getElementById('input-tag');
                        if (inputEl) {
                            inputEl.focus();
                            inputEl.style.transition = 'all 0.3s';
                            inputEl.style.borderColor = 'var(--theme-color)';
                            inputEl.style.boxShadow = '0 0 15px var(--theme-color)';
                            setTimeout(() => {
                                inputEl.style.boxShadow = 'none';
                                inputEl.style.borderColor = '#2A323C';
                            }, 800);
                        }
                    }
                }
            }

            function handleTrackSubmit(event) {
                event.preventDefault();
                let inputEl = document.getElementById('input-tag');
                if(!inputEl) return;
                let tag = inputEl.value.trim().toUpperCase();
                if (!tag) return;
                if (!tag.startsWith('#')) tag = '#' + tag;
                
                // 自動記憶到第一個空位
                if (!localStorage.getItem('acc1') && !localStorage.getItem('acc2') && !localStorage.getItem('acc3')) {
                    localStorage.setItem('acc1', tag);
                }
                
                window.location.href = '/?tag=' + encodeURIComponent(tag);
            }
            
            function showInputForm() {
                document.getElementById('track-form').style.display = 'flex';
                document.getElementById('player-name-display').style.display = 'none';
                document.getElementById('input-tag').focus();
            }

            function setLang(lang) {
                currentLang = lang;
                localStorage.setItem('lang', lang);
                
                const isEn = lang === 'en';
                document.getElementById('lang-zh').style.background = isEn ? 'transparent' : 'var(--theme-color)';
                document.getElementById('lang-zh').style.color = isEn ? '#AAAAAA' : '#121212';
                document.getElementById('lang-en').style.background = isEn ? 'var(--theme-color)' : 'transparent';
                document.getElementById('lang-en').style.color = isEn ? '#121212' : '#AAAAAA';
                
                applyLangText();
                if (appData['current_player']) {
                    render();
                }
            }

            function applyLangText() {
                const t = i18n[currentLang];
                
                let lblTag = document.getElementById('lbl-tag'); if(lblTag) lblTag.innerText = t.tag_lbl;
                let wTitle = document.getElementById('welcome-title'); if(wTitle) wTitle.innerText = t.welcome_t;
                let wDesc = document.getElementById('welcome-desc'); if(wDesc) wDesc.innerHTML = t.welcome_d;
                
                document.getElementById('input-tag').placeholder = currentLang === 'en' ? '#XXXXXXX' : '#XXXXXXX';
                document.getElementById('btn-track').innerText = t.track;
                
                const lblCurrent = document.getElementById('lbl-current-player'); if(lblCurrent) lblCurrent.innerText = t.current_player_lbl;
                const btnRe = document.getElementById('btn-reenter'); if(btnRe) btnRe.innerText = t.btn_reenter;
                
                const sInp = document.getElementById('searchInput');
                if(sInp) sInp.placeholder = t.search_ph;
                const sBtn = document.getElementById('btn-search');
                if(sBtn) sBtn.innerText = t.search_btn;
                
                document.getElementById('footer-cloud').innerHTML = t.footer;
                const btnClear = document.getElementById('btn-clear-cache');
                if (btnClear) btnClear.innerText = t.clear_cache;
                
                const bpt = document.getElementById('btn-page-toggle');
                if (bpt) bpt.innerText = activePage === 'main' ? t.btn_ranked : t.btn_main;
                
                const bDispData = document.getElementById('btn-disp-data');
                if (bDispData) bDispData.title = currentLang === 'en' ? 'Text View' : '文字數據版';
                const bDispBar = document.getElementById('btn-disp-bar');
                if (bDispBar) bDispBar.title = currentLang === 'en' ? 'Bar View' : '進度條狀版';
                
                const bSes = document.getElementById('btn-session');
                if (bSes) bSes.innerText = t.btn_ses;
                const bAll = document.getElementById('btn-all_time');
                if (bAll) bAll.innerText = t.btn_all;

                const tt = document.getElementById('title-trophies'); if(tt) tt.innerText = t.trophies;
                const t3 = document.getElementById('title-3v3'); if(t3) t3.innerText = t.v3v3;
                const te = document.getElementById('title-elo'); if(te) te.innerText = t.elo;
                const ttr = document.getElementById('title-tier'); if(ttr) ttr.innerText = t.tier;
                const terk = document.getElementById('title-elo-rk'); if(terk) terk.innerText = t.elo;
                const ttrrk = document.getElementById('title-tier-rk'); if(ttrrk) ttrrk.innerText = t.tier;
                
                const rs = document.getElementById('refresh-status');
                if (rs) {
                    if (rs.innerText.includes('等待') || rs.innerText.includes('Wait')) rs.innerText = currentLang === 'zh' ? '等待玩家輸入標籤' : 'Waiting for Player Tag';
                    else if (rs.innerText.includes('完成') || rs.innerText.includes('Sync')) rs.innerText = currentLang === 'zh' ? '資料庫同步完成' : 'Database Synced';
                }

                renderTopAccButtons();
            }

            // ✨ 三按鈕常駐顯示，空狀態時變暗
            function renderTopAccButtons() {
                const container = document.getElementById('top-acc-container');
                if (!container) return;
                
                const t = i18n[currentLang];
                const accNames = [t.acc1, t.acc2, t.acc3];
                let html = "";
                
                for(let i=0; i<3; i++) {
                    let tag = localStorage.getItem('acc'+(i+1));
                    let isActive = (currentUrlTag && currentUrlTag === tag) ? 'active' : '';
                    let opacity = tag ? '1' : '0.4'; // 沒設定時變暗
                    let title = tag ? tag : "點擊綁定當前畫面玩家";
                    
                    html += `<button id="btn-acc-${i+1}" class="top-acc-btn ${isActive}" onclick="handleAccClick(${i+1})" title="${title}" style="opacity: ${opacity};">${accNames[i]}</button>`;
                }
                
                container.innerHTML = html;
            }

            function TL(str) {
                if (currentLang === 'zh') return str;
                const map = {
                    '🏅 排位賽': '🏅 Ranked', '⏳ 一般模式': '⏳ Casual', '🎯 挑戰': '🎯 Challenge', '🎪 特別活動': '🎪 Special', '📊 總戰績': '📊 Total',
                    '排位賽': 'Ranked', '一般模式': 'Casual', '挑戰': 'Challenge', '特別活動': 'Special Events',
                    '搶星大作戰': 'Bounty', '寶石爭奪戰': 'Gem Grab', '金庫攻防戰': 'Heist', 
                    '亂鬥足球': 'Brawl Ball', '據點搶奪戰': 'Hot Zone', '極限淘汰賽': 'Knockout'
                };
                return map[str] || str;
            }

            function get_wr_js(w, l, d=0) {
                let total = w + l + d;
                return total > 0 ? (w/total*100).toFixed(1) + '%' : "0.0%";
            }

            function togglePage() {
                activePage = activePage === 'main' ? 'ranked' : 'main';
                sessionStorage.setItem('activePage', activePage);
                applyPageState();
            }

            function applyPageState() {
                document.getElementById('page-main').classList.toggle('active', activePage === 'main');
                document.getElementById('page-ranked').classList.toggle('active', activePage === 'ranked');
                
                const btn = document.getElementById('btn-page-toggle');
                const t = i18n[currentLang];
                if (btn) {
                    if (activePage === 'main') {
                        btn.innerHTML = t.btn_ranked;
                    } else {
                        btn.innerHTML = t.btn_main;
                    }
                }
            }

            function createRowHtml(label, statObj, isSummary = false, isTotal = false) {
                const total = statObj.w + statObj.l + statObj.d;
                let lineClass = isSummary ? 'summary-line' : 'b-line';
                if (isTotal) lineClass += ' is-total';
                
                const displayTxt = statObj.txt || statObj.stats;
                
                if (currentDisplayMode === 'data' || total === 0) {
                    return `<div class="${lineClass}"><span class="b-name">${label}</span><span class="b-data">${displayTxt}</span></div>`;
                } else {
                    const wPct = (statObj.w / total) * 100;
                    const barClass = isSummary ? 'summary-line-bar' + (isTotal ? ' is-total' : '') : 'b-line-bar';
                    
                    let trackHtml = `<div class="bar-track">`;
                    if(statObj.w > 0) trackHtml += `<div class="bar-fill win" style="width: ${wPct}%;"></div>`;
                    trackHtml += `</div>`;
                    
                    return `
                    <div class="${barClass}">
                        <div class="bar-label">
                            <span class="b-name">${label}</span>
                            <span class="b-data">${displayTxt}</span>
                        </div>
                        ${trackHtml}
                    </div>`;
                }
            }

            function renderRankedPage(accData) {
                const container = document.getElementById('ranked-seasons-container');
                if (!container) return;
                container.innerHTML = '';
                const t = i18n[currentLang];
                
                const isSession = (currentView === 'session');
                const seasonsData = isSession ? accData.ranked_seasons_session : accData.ranked_seasons_all_time;
                
                if (!seasonsData || Object.keys(seasonsData).length === 0) {
                    container.innerHTML = `
                        <div style="text-align:center; padding: 50px 20px; background-color:#121212; border-radius:12px; margin-top:20px; border:1px dashed #2A323C;">
                            <div style="font-size:32px; margin-bottom:10px;">${isSession ? '⏳' : '📊'}</div>
                            <div style="font-size:18px; color:#AAA; font-weight:bold;">${isSession ? t.no_rk_ses : t.no_rk_all}</div>
                        </div>`;
                    return;
                }

                const seasons = Object.keys(seasonsData).sort((a,b) => parseInt(b) - parseInt(a));
                
                seasons.forEach(season => {
                    const sData = seasonsData[season];
                    
                    let seasonTitleStr = currentLang === 'zh' ? `第 ${season} 賽季` : `Season ${season}`;
                    let subBadge = isSession ? 
                        `<span style="font-size:14px; color:var(--theme-color); padding: 2px 8px; border: 1px solid var(--theme-color); border-radius: 4px; margin-left:10px;">${t.ses_match}</span>` :
                        ((sData.start_date && sData.end_date) ? ` <span style="font-size:18px; color:#AAAAAA;">(${sData.start_date} ~ ${sData.end_date})</span>` : "");
                    
                    let sHtml = `<div class="season-section">
                        <h2 style="color:var(--theme-color); border-bottom: 2px solid #2A323C; padding-bottom: 10px; margin-top: 30px;">
                            🏆 ${seasonTitleStr}${subBadge} <span style="font-size:16px; color:#888; float:right; line-height: 28px;">${sData.w}W - ${sData.l}L (${get_wr_js(sData.w, sData.l, sData.d)})</span>
                        </h2>
                        <div class="brawler-grid">`;
                    
                    const modeColors = { '搶星大作戰': '#01cfff', '寶石爭奪戰': '#9b3df3', '金庫攻防戰': '#d65cd3', '亂鬥足球': '#8ca0df', '據點搶奪戰': '#e33c50', '極限淘汰賽': '#f7831c' };
                    
                    TARGET_SIX_MODES.forEach(modeName => {
                        let totalMatches = 0;
                        let brawlers = [];
                        
                        for (const [bName, bData] of Object.entries(sData.brawlers)) {
                            if (bData.modes && bData.modes[modeName]) {
                                let modeData = bData.modes[modeName];
                                let m = modeData.w + modeData.l + modeData.d;
                                totalMatches += m;
                                brawlers.push({ name: bName, w: modeData.w, l: modeData.l, d: modeData.d, matches: m, wr: modeData.w / m });
                            }
                        }
                        
                        brawlers.forEach(b => b.pr = totalMatches > 0 ? b.matches / totalMatches : 0);
                        
                        let color = modeColors[modeName] || '#FFFFFF';
                        let mHtml = `<div class="brawler-cat" style="border-top: 4px solid ${color};">
                            <h3 style="color: ${color}; margin-bottom: 5px;">${TL(modeName)}</h3>
                            <div style="text-align: right; font-size: 14px; color: #888; font-family: Consolas; margin-bottom: 15px;">${t.total_match}${totalMatches}</div>`;
                            
                        if (isSession) {
                            if (brawlers.length === 0) {
                                mHtml += `<div style="color:#777; text-align:center; padding: 30px 0;">${t.no_hero}</div>`;
                            } else {
                                let hSes = t.hero_ses.replace('{n}', totalMatches);
                                mHtml += `<div style="color:#DDD; font-size:14px; margin: 10px 0 8px 0; font-weight:bold;">${hSes}</div>`;
                                brawlers.sort((a, b) => b.matches - a.matches || b.wr - a.wr);
                                brawlers.forEach(b => {
                                    mHtml += `<div class="b-line-bar"><div class="bar-label"><span class="b-name">🦸 ${b.name}</span><span class="b-data">${(b.wr*100).toFixed(1)}% (${b.w}W-${b.l}L)</span></div><div class="bar-track"><div class="bar-fill win" style="width: ${b.wr*100}%; background-color: ${color};"></div></div></div>`;
                                });
                            }
                        } else {
                            let valid = brawlers.filter(b => b.matches >= 3);
                            let topPR = [...valid].sort((a, b) => b.pr - a.pr).slice(0, 3);
                            let topWR = [...valid].sort((a, b) => b.wr - a.wr || b.matches - a.matches).slice(0, 3);
                            let trap = [...valid].filter(b => b.wr < 0.45).sort((a, b) => b.matches - a.matches)[0];
                            let gem = [...valid].filter(b => b.wr >= 0.70 && !topPR.some(b2 => b2.name === b.name)).sort((a, b) => b.wr - a.wr || b.matches - a.matches)[0];
                            
                            if (valid.length === 0) {
                                mHtml += `<div style="color:#777; text-align:center; padding: 30px 0;">${t.req_3}</div>`;
                            } else {
                                mHtml += `<div style="color:#DDD; font-size:14px; margin: 10px 0 5px 0;">${t.pr_top}</div>`;
                                topPR.forEach(b => { mHtml += `<div class="b-line-bar"><div class="bar-label"><span class="b-name">🦸 ${b.name}</span><span class="b-data">${(b.pr*100).toFixed(1)}% (${b.matches}${currentLang==='zh'?'場':''})</span></div><div class="bar-track"><div class="bar-fill" style="width: ${b.pr*100}%; background-color: #888888;"></div></div></div>`; });
                                
                                mHtml += `<div style="color:#DDD; font-size:14px; margin: 20px 0 5px 0;">${t.wr_top}</div>`;
                                topWR.forEach(b => { mHtml += `<div class="b-line-bar"><div class="bar-label"><span class="b-name">🦸 ${b.name}</span><span class="b-data">${(b.wr*100).toFixed(1)}% (${b.w}W-${b.l}L)</span></div><div class="bar-track"><div class="bar-fill win" style="width: ${b.wr*100}%; background-color: ${color};"></div></div></div>`; });
                                
                                if (trap || gem) {
                                    mHtml += `<div style="margin-top: 25px; padding: 12px; background-color: #1A1F24; border-radius: 6px; border-left: 3px solid #2A323C;">`;
                                    if (trap) mHtml += `<div style="margin-bottom: ${gem ? '12px' : '0'};"><div style="color:#FF5555; font-size:13px; font-weight:bold;">${t.trap}</div><div style="display:flex; justify-content:space-between; margin-top:5px; font-family:Consolas;"><span class="b-name">🦸 ${trap.name}</span><span style="color:#FFF;">${(trap.wr*100).toFixed(1)}% ${t.wr}</span></div></div>`;
                                    if (gem) mHtml += `<div><div style="color:#00FFAA; font-size:13px; font-weight:bold;">${t.gem}</div><div style="display:flex; justify-content:space-between; margin-top:5px; font-family:Consolas;"><span class="b-name">🦸 ${gem.name}</span><span style="color:#FFF;">${(gem.wr*100).toFixed(1)}% ${t.wr}</span></div></div>`;
                                    mHtml += `</div>`;
                                }
                            }
                        }
                        mHtml += `</div>`;
                        sHtml += mHtml;
                    });
                    sHtml += `</div></div>`; 
                    container.innerHTML += sHtml;
                });
            }

            function handleSearch() {
                const searchInput = document.getElementById('searchInput');
                if(!searchInput) return;
                const query = searchInput.value.trim();
                if (!query) return;
                
                const t = i18n[currentLang];
                const isChinese = /[\\u4e00-\\u9fff]/.test(query);
                const searchData = appData['current_player']['all_time'];
                let resultHtml = "";
                let modalTitle = "";
                const modalBox = document.getElementById('modal-content-box');

                if (isChinese) {
                    modalTitle = t.modal_tot;
                    if (modalBox) modalBox.style.maxWidth = "500px"; 
                    
                    resultHtml += `<div class="map-view-grid">`;
                    const mapCategories = [['🏅', '排位賽'], ['⏳', '一般模式']]; 
                    mapCategories.forEach(([icon, cat]) => {
                        let catData = searchData.map_stats.find(c => c.title === cat);
                        if (catData) {
                            resultHtml += `<div class="brawler-cat"><h3>${icon} ${TL(cat)} <span style="float:right; color:var(--theme-color); font-family:Consolas;">${catData.wr}</span></h3>`;
                            resultHtml += createRowHtml(t.cat_tot, {stats: `${catData.wins}W - ${catData.losses}L`, w: catData.w, l: catData.l, d: catData.d}, true);
                            catData.modes.forEach(m => {
                                resultHtml += createRowHtml(`• ${TL(m.name)}`, m);
                            });
                            resultHtml += `</div>`;
                        }
                    });
                    resultHtml += `</div>`;
                } else {
                    if (modalBox) modalBox.style.maxWidth = "500px";
                    const bName = Object.keys(searchData.brawler_details).find(k => k.includes(query.toUpperCase()));
                    if (!bName) {
                        alert(t.modal_not_found.replace('{q}', query));
                        return;
                    }
                    const bStats = searchData.brawler_details[bName];
                    modalTitle = currentLang === 'zh' ? `【 ${bName} 】(歷史總計)` : `[ ${bName} ] (All-Time)`;
                    let totalRankedMatches = searchData.summary.ranked.w + searchData.summary.ranked.l + searchData.summary.ranked.d;

                    let sum_title = currentLang === 'zh' ? '▶ 總結' : '▶ Summary';
                    resultHtml += `<div class="brawler-cat" style="border-left-color:#FFAA00;"><h3>${sum_title} <span style="float:right; color:#FFAA00; font-family:Consolas;">${bStats.summary.split('(')[1].replace(')','')}</span></h3>`;
                    resultHtml += createRowHtml(t.sum_wl, {stats: bStats.summary.split('(')[0].trim(), w: bStats.w, l: bStats.l, d: bStats.d});
                    resultHtml += `</div>`;
                    
                    bStats.cats.forEach(cat => {
                        let prText = "";
                        if (cat.title === '排位賽') {
                            let catMatches = cat.w + cat.l + cat.d;
                            let pr = totalRankedMatches > 0 ? ((catMatches / totalRankedMatches) * 100).toFixed(1) : "0.0";
                            prText = ` <span style="font-size:14px; color:#888;">(${t.pr}: ${pr}%)</span>`;
                        }
                        resultHtml += `<div class="brawler-cat"><h3>${cat.icon} ${TL(cat.title)}${prText} <span style="float:right; color:var(--theme-color); font-family:Consolas;">${cat.wr}</span></h3>`;
                        resultHtml += createRowHtml(t.cat_tot, {stats: `${cat.wins}W - ${cat.losses}L`, w: cat.w, l: cat.l, d: cat.d});
                        cat.modes.forEach(m => {
                            resultHtml += createRowHtml(`• ${TL(m.name)}`, m);
                        });
                        resultHtml += `</div>`;
                    });
                }
                
                document.getElementById('modal-title').innerText = modalTitle;
                document.getElementById('modal-body').innerHTML = resultHtml;
                document.getElementById('searchModal').style.display = 'flex';
                document.body.classList.add('no-scroll');
                searchInput.value = '';
            }

            function closeModal() {
                document.getElementById('searchModal').style.display = 'none';
                document.body.classList.remove('no-scroll');
            }

            function render() {
                if (!currentUrlTag) return;

                const data = appData['current_player'];
                const viewData = data[currentView];
                const isSession = (currentView === 'session');
                const t = i18n[currentLang];
                
                document.documentElement.style.setProperty('--theme-color', data.color);
                applyPageState();
                
                // ✨ 修正：拔除 API 失敗卡死機制，永遠都會顯示名字或代碼
                let displayTitle = data.name ? data.name : currentUrlTag;
                document.getElementById('track-form').style.display = 'none';
                document.getElementById('player-name-display').style.display = 'flex';
                document.getElementById('val-player-name').innerText = displayTitle;
                
                ['btn-session', 'btn-all_time', 'btn-disp-data', 'btn-disp-bar'].forEach(id => {
                    const el = document.getElementById(id);
                    if(!el) return;
                    if(id.includes('session')) { el.classList.toggle('active', isSession); el.style.color = isSession ? data.color : '#555555'; }
                    if(id.includes('all_time')) { el.classList.toggle('active', !isSession); el.style.color = !isSession ? data.color : '#555555'; }
                    if(id.includes('data')) { el.classList.toggle('active', currentDisplayMode === 'data'); el.style.color = currentDisplayMode === 'data' ? data.color : '#555555'; }
                    if(id.includes('bar')) { el.classList.toggle('active', currentDisplayMode === 'bar'); el.style.color = currentDisplayMode === 'bar' ? data.color : '#555555'; }
                });

                const tierStr = data.tier.toUpperCase();
                let tierColor = data.color; 
                if (tierStr.includes('BRONZE')) tierColor = '#CD7F32';      
                else if (tierStr.includes('SILVER')) tierColor = '#B4C5E4'; 
                else if (tierStr.includes('GOLD')) tierColor = '#FFD700';   
                else if (tierStr.includes('DIAMOND')) tierColor = '#11C4EB';
                else if (tierStr.includes('MYTHIC')) tierColor = '#DF44FF'; 
                else if (tierStr.includes('LEGENDARY')) tierColor = '#FF3333'; 
                else if (tierStr.includes('MASTER')) tierColor = '#FF8800'; 
                
                let eloDisplay = data.elo === '0' ? '-' : data.elo;
                let trophyDisplay = data.trophies === 0 ? '-' : data.trophies;
                let v3v3Display = data.victories_3v3 === 0 ? '-' : data.victories_3v3;
                let tierDisplay = data.tier === "UNKNOWN" ? '-' : data.tier;

                document.getElementById('val-trophies').innerHTML = `${trophyDisplay} <span class="diff">(${data.diff_trophies})</span>`;
                document.getElementById('val-3v3').innerText = v3v3Display;
                
                const eloDiffStr = data.diff_elo || '+0';
                document.getElementById('val-elo').innerHTML = `${eloDisplay} <span class="diff">(${eloDiffStr})</span>`;
                document.getElementById('val-elo-rk').innerHTML = `${eloDisplay} <span class="diff">(${eloDiffStr})</span>`;
                
                const tierElem = document.getElementById('val-tier');
                tierElem.innerText = tierDisplay;
                tierElem.style.color = tierColor;
                tierElem.style.textShadow = `0 0 15px ${tierColor}90`;
                
                const tierElemRk = document.getElementById('val-tier-rk');
                tierElemRk.innerText = tierDisplay;
                tierElemRk.style.color = tierColor;
                tierElemRk.style.textShadow = `0 0 15px ${tierColor}90`;
                
                document.getElementById('summary-section').innerHTML = `
                    ${createRowHtml(TL('🏅 排位賽'), viewData.summary.ranked, true)}
                    ${createRowHtml(TL('⏳ 一般模式'), viewData.summary.casual, true)}
                    ${createRowHtml(TL('🎪 特別活動'), viewData.summary.special, true)}
                    ${createRowHtml(TL('📊 總戰績'), viewData.summary.total, true, true)}
                `;
                
                const rkLabel = isSession ? t.rk_ses : t.rk_all;
                document.getElementById('summary-ranked-only').innerHTML = createRowHtml(rkLabel, viewData.summary.ranked, true);

                const grid = document.getElementById('brawler-grid');
                grid.innerHTML = '';
                viewData.brawlers.forEach(cat => {
                    let catHtml = `<div class="brawler-cat"><h3>${cat.icon} ${TL(cat.title)}</h3>`;
                    cat.items.forEach(b => {
                        catHtml += createRowHtml(`🦸 ${b.name}`, b);
                    });
                    catHtml += `</div>`;
                    grid.innerHTML += catHtml;
                });
                
                renderRankedPage(data);
            }

            function switchView(view) { currentView = view; sessionStorage.setItem('currentView', view); render(); }
            function setDisplayMode(mode) { currentDisplayMode = mode; localStorage.setItem('displayMode', mode); render(); }
            function setAlignment(align) {
                currentAlign = align; localStorage.setItem('pageAlign', align);
                document.body.style.justifyContent = align;
                document.getElementById('btn-align-left').classList.toggle('active', align === 'flex-start');
                document.getElementById('btn-align-center').classList.toggle('active', align === 'center');
                document.getElementById('btn-align-right').classList.toggle('active', align === 'flex-end');
            }

            setLang(currentLang);
            setAlignment(currentAlign);
        </script>
    </body>
    </html>
    """
    
    final_html = html_template.replace('__APP_DATA_HERE__', js_string)
    final_html = final_html.replace('__CURRENT_TAG__', tag)
    final_html = final_html.replace('__DASHBOARD_DISPLAY_NAV__', dashboard_display_nav)
    final_html = final_html.replace('__DASHBOARD_DISPLAY__', dashboard_display)
    final_html = final_html.replace('__DASHBOARD_DISPLAY_SEARCH__', dashboard_display_search)
    final_html = final_html.replace('__WELCOME_DISPLAY__', welcome_display)
    final_html = final_html.replace('__REFRESH_TEXT__', refresh_status_text)

    return HTMLResponse(content=final_html)
