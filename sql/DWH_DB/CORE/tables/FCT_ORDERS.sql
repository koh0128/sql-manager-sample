CREATE OR REPLACE TABLE DWH_DB.CORE.FCT_ORDERS (
    ORDER_ID      NUMBER(38,0) NOT NULL COMMENT '注文ID（主キー）',
    CUSTOMER_ID   NUMBER(38,0)          COMMENT '顧客ID',
    ORDER_DATE    DATE                  COMMENT '注文日（JST）',
    ORDER_AMOUNT  NUMBER(12,2)          COMMENT '税込注文金額',
    ORDER_STATUS  VARCHAR               COMMENT '大文字に正規化したステータス',
    CHANNEL       VARCHAR
);
