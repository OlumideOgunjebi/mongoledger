-- ===========================================================================
-- COMP.8157 Advanced Database Topics, Fall 2026, University of Windsor
-- Team # 3: A Change Stream Ledger for MongoDB
-- Repository: github.com/OlumideOgunjebi/mongoledger
-- File:    sqlserver/01_schema.sql
-- Owner:   Rohith Nelluri (database lead)
-- Purpose: SQL Server schema: plain table and updatable ledger table for
--          SmallBank
-- Covers:  FR21; acceptance test AC11 (see docs/D.4.1)
-- ===========================================================================

/* SQL Server 2022 baseline: the same SmallBank and YCSB data in plain tables and
   in updatable ledger tables. Run with: python -m sqlserver.ledger_baseline schema */

IF DB_ID(N'LedgerBench') IS NULL
    CREATE DATABASE LedgerBench;
GO
USE LedgerBench;
GO

/* SmallBank: one row per customer, balances in cents */
IF OBJECT_ID(N'dbo.bank_plain', N'U') IS NULL
    CREATE TABLE dbo.bank_plain (
        custid   BIGINT       NOT NULL PRIMARY KEY,
        name     NVARCHAR(64) NOT NULL,
        savings  BIGINT       NOT NULL,
        checking BIGINT       NOT NULL
    );
GO

IF OBJECT_ID(N'dbo.bank_ledger', N'U') IS NULL
    CREATE TABLE dbo.bank_ledger (
        custid   BIGINT       NOT NULL PRIMARY KEY,
        name     NVARCHAR(64) NOT NULL,
        savings  BIGINT       NOT NULL,
        checking BIGINT       NOT NULL
    )
    WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.bank_ledger_history), LEDGER = ON);
GO

/* YCSB JDBC binding: key plus ten fields */
IF OBJECT_ID(N'dbo.usertable_plain', N'U') IS NULL
    CREATE TABLE dbo.usertable_plain (
        YCSB_KEY VARCHAR(255) NOT NULL PRIMARY KEY,
        FIELD0 VARCHAR(100), FIELD1 VARCHAR(100), FIELD2 VARCHAR(100), FIELD3 VARCHAR(100), FIELD4 VARCHAR(100),
        FIELD5 VARCHAR(100), FIELD6 VARCHAR(100), FIELD7 VARCHAR(100), FIELD8 VARCHAR(100), FIELD9 VARCHAR(100)
    );
GO

IF OBJECT_ID(N'dbo.usertable_ledger', N'U') IS NULL
    CREATE TABLE dbo.usertable_ledger (
        YCSB_KEY VARCHAR(255) NOT NULL PRIMARY KEY,
        FIELD0 VARCHAR(100), FIELD1 VARCHAR(100), FIELD2 VARCHAR(100), FIELD3 VARCHAR(100), FIELD4 VARCHAR(100),
        FIELD5 VARCHAR(100), FIELD6 VARCHAR(100), FIELD7 VARCHAR(100), FIELD8 VARCHAR(100), FIELD9 VARCHAR(100)
    )
    WITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE = dbo.usertable_ledger_history), LEDGER = ON);
GO
