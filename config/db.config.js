const { createClient } = require('@supabase/supabase-js');
require('dotenv').config();

const supabaseUrl = process.env.SUPABASE_URL;
const supabaseKey = process.env.SUPABASE_SERVICE_KEY; // Supabase "service_role" 키 사용 (보안 주의)

const supabase = createClient(supabaseUrl, supabaseKey);

module.exports = supabase;
