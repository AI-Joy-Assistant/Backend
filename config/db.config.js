const {Pool} = require('pg')
const pgp = require('pg-promise')()
require("dotenv").config();

class Database {
    constructor() {
        this.pool = new Pool({
            user: process.env.DB_USER,
            host: process.env.DB_HOST,
            database: process.env.DB_NAME,
            password: process.env.DB_PASS,
            port: process.env.DB_PORT,
            max: 30,
            idleTimeoutMillis: 10000,
            connectionTimeoutMillis: 60000
        });
    }

    /**
     * Get a client connection from the pool.
     */
    async connect() {
        try {
            const client = await this.pool.connect();
            return client;
        } catch (error) {
            console.error("Database connection error:", error);
            throw error;
        }
    }

    async query(query, params) {
        try {
            if (params) return await this.pool.query(query, params).then((dbRes) => {
                return dbRes
            })
            return await this.pool.query(query).then((dbRes => {
                return dbRes
            }))
        } catch (error) {
            console.log('QUERY Error:', error)
            throw error
        }
    }

    async begin(client) {
        if (!this.pool) {
            console.log("no pool")
            throw new Error('this.pool is undefined. Ensure the pool is properly initialized.');
        }
        try {
            await client.query('BEGIN');
        } catch (error) {
            console.error('Transaction BEGIN Error:', error);
            throw error;
        }
    }


    async commit(client) {
        try {
            await client.query('COMMIT');
        } catch (error) {
            console.error('Transaction COMMIT Error:', error);
            throw error;
        }
    }

    async rollback(client) {
        try {
            await client.query('ROLLBACK');
        } catch (error) {
            console.error('Transaction ROLLBACK Error:', error);
            throw error;
        }
    }

    async end() {
        await this.pool.end()
    }
}

module.exports = Database