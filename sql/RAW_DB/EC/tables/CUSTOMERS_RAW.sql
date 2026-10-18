CREATE OR REPLACE TABLE RAW_DB.EC.CUSTOMERS_RAW (
    CUSTOMER_ID  NUMBER(38,0) NOT NULL COMMENT '顧客ID',
    EMAIL        VARCHAR               COMMENT 'メールアドレス（PII）',
    PREF         VARCHAR               COMMENT '都道府県コード',
    SIGNUP_DATE  DATE                  COMMENT '会員登録日',
    BIRTH_DATE   DATE                  COMMENT '生年月日（PII）'
);
