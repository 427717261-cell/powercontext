-- 在 seekdb 上建「怪谈档案库」与最小权限账号。
--
-- 用 seekdb 的 root 账号执行：
--     mysql -h 127.0.0.1 -P 2881 -u root -p < init_account.sql
--
-- 执行前请把下面两处占位值替换成自己的口令：
--     <radio_口令>       —— 电台检索后端用的账号口令
--     <seekdb_root_口令> —— 如需修改 root 口令，把 ALTER USER 那行的占位值一并替换
--
-- 账号设计：radio 账号只有 radio_archive 库上的权限，不给全局权限，
-- 与「Dify 容器 → seekdb」的最小权限访问一致。

CREATE DATABASE IF NOT EXISTS `radio_archive`
  DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin;

CREATE USER IF NOT EXISTS 'radio'@'%' IDENTIFIED BY '<radio_口令>';
ALTER USER 'radio'@'%' IDENTIFIED BY '<radio_口令>';

GRANT ALL PRIVILEGES ON `radio_archive`.* TO 'radio'@'%';
FLUSH PRIVILEGES;

-- 可选：收紧 root 的登录方式（默认允许 root@'%' 用口令远程登录）。
-- 如果你的环境不需要远程 root，建议改为只保留 'root'@'localhost'：
-- ALTER USER 'root'@'%' IDENTIFIED BY '<seekdb_root_口令>';
-- DROP USER IF EXISTS 'root'@'%';

-- 确认结果（应能看到 radio 账号与 radio_archive 库）
SHOW DATABASES LIKE 'radio_archive';
SHOW GRANTS FOR 'radio'@'%';
