# -*- coding: utf-8 -*-
"""设置保存 API（自 control_window.py 抽出的 mixin）。

53 个 `save_*` 为纯转发 `core.settings` 的薄方法；`save_task_modes` /
`save_task_count` 带「任务运行中拒绝修改」守卫。`save_message_config`
属消息服务编排，在 message_api.py。

保持显式方法（而非 setattr 动态生成）：callPython 名称不变，同时
Pylance 能解析 `self.expose(self.save_*)`，避免编辑器满屏飘红。

注意：本 mixin 方法按本模块的 `settings_module` 解析设置函数；测试
monkeypatch 请 patch `core.settings` 或本模块的 `settings_module`，
patch `control_window` 上的同名导入不会影响这里的转发。
"""
from ..core import settings as settings_module
from ..core import user_data
from . import window_registry
from .host_contract import ControlHost


class SettingsApiMixin(ControlHost):
    def _selected_role_running(self):
        """选中窗口自身的任务是否在跑（多窗口：只锁该角色设置）。"""
        role = window_registry.selected_role() or user_data.current_role_id()
        return self._tm.is_running(role)
    def save_auto_famen(self, on):
        """战斗设置变更：保存自动使用法门开关状态。"""
        return settings_module.save_auto_famen(on)

    def save_auto_refill(self, on):
        """通用设置变更：保存战斗结束自动补血量蓝量开关状态。"""
        return settings_module.save_auto_refill(on)

    def save_battle_action(self, target, value):
        """战斗设置变更：保存人物/召唤兽默认战斗动作。"""
        return settings_module.save_battle_action(target, value)

    def save_battle_only_auto_rejoin(self, on):
        """仅战斗设置变更：保存自动归队开关状态。"""
        return settings_module.save_battle_only_auto_rejoin(on)

    def save_battle_only_hezi(self, on):
        """仅战斗设置变更：保存自动抢盒子开关状态。"""
        return settings_module.save_battle_only_hezi(on)

    def save_clear_screen(self, on):
        """通用设置变更：保存启动清屏开关状态。"""
        return settings_module.save_clear_screen(on)

    def save_debug_overlay(self, on):
        """调试信息开关变更：保存开关状态（悬浮层由任务侧读取、
        诊断导航显隐由页面侧同步）。"""
        return settings_module.save_debug_overlay(on)

    def save_debug_ui_unlocked(self, on):
        """「显示与调试」卡片解锁（关于页连点版本号 6 次）：保存
        解锁标记，解锁后下次启动直接展示该卡片。"""
        return settings_module.save_debug_ui_unlocked(on)

    def save_high_perf(self, on):
        """通用设置变更：保存高性能模式开关状态。"""
        return settings_module.save_high_perf(on)

    def save_site_coord_nav(self, on):
        """通用设置变更：保存任务点位坐标化寻路开关。

        关闭时任务点位寻路回滚为原固定小地图像素点击逻辑（默认开启）。
        """
        return settings_module.save_site_coord_nav(on)

    def save_silent_mode(self, on):
        """通用设置变更：保存静默操作开关状态（主进程立即生效）。"""
        return settings_module.save_silent_mode(on)

    def save_threshold(self, name, value):
        """阈值设置变更：保存某条血蓝阈值。"""
        return settings_module.save_threshold(name, value)

    def save_ui_theme(self, theme):
        """主题切换：保存浅色/深色选择。"""
        return settings_module.save_ui_theme(theme)

    def save_use_sheyaoxiang(self, on):
        """摄妖香开关变更：保存开关状态。"""
        return settings_module.save_use_sheyaoxiang(on)

    def save_wubei_auto_create_team(self, on):
        """五倍设置变更：保存自动创建队伍开关状态。"""
        return settings_module.save_wubei_auto_create_team(on)

    def save_wubei_auto_kick_away(self, on):
        """五倍设置变更：保存暂离超时自动请离开关状态。"""
        return settings_module.save_wubei_auto_kick_away(on)

    def save_wubei_auto_rejoin(self, on):
        """五倍设置变更：保存自动归队开关状态。"""
        return settings_module.save_wubei_auto_rejoin(on)

    def save_wubei_auto_repair(self, on):
        """五倍设置变更：保存自动修理装备开关状态。"""
        return settings_module.save_wubei_auto_repair(on)

    def save_wubei_auto_wuyi(self, on):
        """五倍设置变更：保存自动巫医开关状态。"""
        return settings_module.save_wubei_auto_wuyi(on)

    def save_wubei_check_team(self, on):
        """五倍设置变更：保存检查队伍开关状态。"""
        return settings_module.save_wubei_check_team(on)

    def save_wubei_hezi(self, on):
        """五倍设置变更：保存自动抢盒子开关状态。"""
        return settings_module.save_wubei_hezi(on)

    def save_wubei_kick_away_minutes(self, value):
        """五倍设置变更：保存暂离超时（分钟）。"""
        return settings_module.save_wubei_kick_away_minutes(value)

    def save_wubei_kuimulang_switch(self, on):
        """五倍设置变更：保存领取奎木狼任务开关状态。"""
        return settings_module.save_wubei_kuimulang_switch(on)

    def save_wubei_manual_nav(self, on):
        """五倍设置变更：保存兼容无快捷寻路开关状态。"""
        return settings_module.save_wubei_manual_nav(on)

    def save_wubei_force_manual(self, on):
        """五倍设置变更：保存强制手动寻路（忽略快捷寻路）开关状态。"""
        return settings_module.save_wubei_force_manual(on)

    def save_wubei_repair_interval(self, value):
        """五倍设置变更：保存自动修理间隔（场战斗）。"""
        return settings_module.save_wubei_repair_interval(value)

    def save_wubei_shout_switch(self, on):
        """五倍设置变更：保存缺人自动喊话开关状态。"""
        return settings_module.save_wubei_shout_switch(on)

    def save_wubei_shout_text(self, text):
        """五倍设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return settings_module.save_wubei_shout_text(text)

    def save_wubei_tab_group(self, on):
        """五倍设置变更：保存组号专用开关状态。"""
        return settings_module.save_wubei_tab_group(on)

    def save_wubei_wuyi_interval(self, value):
        """五倍设置变更：保存自动巫医间隔（轮）。"""
        return settings_module.save_wubei_wuyi_interval(value)

    def save_wuhuan_vitality_continue(self, on):
        """五环设置变更：保存活力不足继续任务开关状态。"""
        return settings_module.save_wuhuan_vitality_continue(on)

    def save_xiuluo_auto_create_team(self, on):
        """修罗设置变更：保存自动创建队伍开关状态。"""
        return settings_module.save_xiuluo_auto_create_team(on)

    def save_xiuluo_auto_kick_away(self, on):
        """修罗设置变更：保存暂离超时自动请离开关状态。"""
        return settings_module.save_xiuluo_auto_kick_away(on)

    def save_xiuluo_auto_rejoin(self, on):
        """修罗设置变更：保存自动归队开关状态。"""
        return settings_module.save_xiuluo_auto_rejoin(on)

    def save_xiuluo_auto_repair(self, on):
        """修罗设置变更：保存自动修理装备开关状态。"""
        return settings_module.save_xiuluo_auto_repair(on)

    def save_xiuluo_auto_wuyi(self, on):
        """修罗设置变更：保存自动巫医开关状态。"""
        return settings_module.save_xiuluo_auto_wuyi(on)

    def save_xiuluo_check_team(self, on):
        """修罗设置变更：保存检查队伍开关状态。"""
        return settings_module.save_xiuluo_check_team(on)

    def save_xiuluo_hezi(self, on):
        """修罗设置变更：保存自动抢盒子开关状态。"""
        return settings_module.save_xiuluo_hezi(on)

    def save_xiuluo_kick_away_minutes(self, value):
        """修罗设置变更：保存暂离超时（分钟）。"""
        return settings_module.save_xiuluo_kick_away_minutes(value)

    def save_xiuluo_force_manual(self, on):
        """修罗设置变更：保存强制手动寻路（忽略快捷寻路）开关状态。"""
        return settings_module.save_xiuluo_force_manual(on)

    def save_xiuluo_manual_nav(self, on):
        """修罗设置变更：保存兼容无快捷寻路（手动寻路）开关状态。"""
        return settings_module.save_xiuluo_manual_nav(on)

    def save_xiuluo_min(self, value):
        """修罗设置变更：保存最少人数。"""
        return settings_module.save_xiuluo_min(value)

    def save_xiuluo_races(self, races):
        """修罗设置变更：保存必须有的种族列表。"""
        return settings_module.save_xiuluo_races(races)

    def save_xiuluo_repair_interval(self, value):
        """修罗设置变更：保存自动修理间隔（场战斗）。"""
        return settings_module.save_xiuluo_repair_interval(value)

    def save_xiuluo_shout_switch(self, on):
        """修罗设置变更：保存缺人自动喊话开关状态。"""
        return settings_module.save_xiuluo_shout_switch(on)

    def save_xiuluo_shout_text(self, text):
        """修罗设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return settings_module.save_xiuluo_shout_text(text)

    def save_xiuluo_tab_group(self, on):
        """修罗设置变更：保存组号专用开关状态。"""
        return settings_module.save_xiuluo_tab_group(on)

    def save_xiuluo_wuyi_interval(self, value):
        """修罗设置变更：保存自动巫医间隔（轮）。"""
        return settings_module.save_xiuluo_wuyi_interval(value)

    def save_zhongzu_auto_create_team(self, on):
        """种族比武设置变更：保存自动创建队伍开关状态。"""
        return settings_module.save_zhongzu_auto_create_team(on)

    def save_zhongzu_auto_rejoin(self, on):
        """种族比武设置变更：保存自动归队开关状态。"""
        return settings_module.save_zhongzu_auto_rejoin(on)

    def save_zhongzu_auto_repair(self, on):
        """种族比武设置变更：保存自动修理装备开关状态。"""
        return settings_module.save_zhongzu_auto_repair(on)

    def save_zhongzu_auto_wuyi(self, on):
        """种族比武设置变更：保存自动巫医开关状态。"""
        return settings_module.save_zhongzu_auto_wuyi(on)

    def save_zhongzu_check_team(self, on):
        """种族比武设置变更：保存检查队伍开关状态。"""
        return settings_module.save_zhongzu_check_team(on)

    def save_zhongzu_repair_interval(self, value):
        """种族比武设置变更：保存自动修理间隔（场战斗）。"""
        return settings_module.save_zhongzu_repair_interval(value)

    def save_zhongzu_shout_switch(self, on):
        """种族比武设置变更：保存缺人自动喊话开关状态。"""
        return settings_module.save_zhongzu_shout_switch(on)

    def save_zhongzu_shout_text(self, text):
        """种族比武设置变更：保存喊话内容（返回规范化后的生效内容）。"""
        return settings_module.save_zhongzu_shout_text(text)

    def save_zhongzu_tab_group(self, on):
        """种族比武设置变更：保存组号专用开关状态。"""
        return settings_module.save_zhongzu_tab_group(on)

    def save_zhongzu_wuyi_interval(self, value):
        """种族比武设置变更：保存自动巫医间隔（轮）。"""
        return settings_module.save_zhongzu_wuyi_interval(value)

    def save_task_modes(self, modes):
        """任务多选变更：保存选中列表。任务运行中拒绝修改（只能停止后
        修改，用户要求；页面侧同样禁用控件，此为双保险）。"""
        if self._selected_role_running():
            return {"status": "error", "msg": "该窗口任务运行中，请先停止该窗口任务再修改"}
        return settings_module.save_selected_modes(modes)

    def save_task_count(self, mode, value):
        """任务次数变更：保存某模式的执行次数。任务运行中拒绝修改
        （只能停止后修改，用户要求；页面侧同样禁用控件，此为双保险）。"""
        if self._selected_role_running():
            return {"status": "error", "msg": "该窗口任务运行中，请先停止该窗口任务再修改"}
        return settings_module.save_task_count(mode, value)
