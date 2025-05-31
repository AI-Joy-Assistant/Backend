const { createClient } = require('@supabase/supabase-js');
require('dotenv').config();

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_KEY; // Supabase "service_role" 키 사용 (보안 주의)

// 디버그 로그 추가
console.log('Supabase URL:', supabaseUrl);
console.log('Supabase Key exists:', !!supabaseKey);

if (!supabaseUrl || !supabaseKey) {
    throw new Error('Supabase URL and Key must be provided in .env file');
}

const supabase = createClient(supabaseUrl, supabaseKey, {
    auth: {
        autoRefreshToken: true,
        persistSession: true,
        detectSessionInUrl: true
    }
});

// 연결 테스트
supabase.from('users').select('count').single()
    .then(({ data, error }) => {
        if (error) {
            console.error('Supabase connection test failed:', error);
        } else {
            console.log('Supabase connection successful');
        }
    })
    .catch(error => {
        console.error('Supabase connection error:', error);
    });

module.exports = supabase;
