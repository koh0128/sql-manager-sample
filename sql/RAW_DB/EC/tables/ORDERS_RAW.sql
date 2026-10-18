CREATE OR REPLACE TABLE RAW_DB.EC.ORDERS_RAW (
    ORDER_ID     NUMBER(38,0)  NOT NULL COMMENT '注文ID',
    CUSTOMER_ID  NUMBER(38,0)           COMMENT '顧客ID',
    ORDER_TS     TIMESTAMP_NTZ          COMMENT '注文日時（JST）',
    TOTAL        NUMBER(12,2)           COMMENT '税込合計金額',
    STATUS       VARCHAR                COMMENT '注文ステータス（小文字混在）',
    CHANNEL      VARCHAR
);
