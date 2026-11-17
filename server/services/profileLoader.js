// Builds the matching profile (same shape as match_engine Profile) from the NitiAI tables.
// Used by the JS fallback scorer and by career recommendations.
const pool = require("../config/dbConfig");

async function loadMatchProfile(userId) {
  const [u, ri, sk, pref, exp] = await Promise.all([
    pool.query(
      "SELECT location, preferred_work_mode, career_goal_short, professional_title, years_of_experience FROM users WHERE id = $1",
      [userId]
    ),
    pool.query("SELECT professional_title, years_of_experience, technical_skills FROM resume_info WHERE user_id = $1", [userId]),
    pool.query("SELECT skill_name, proficiency FROM skills WHERE user_id = $1", [userId]),
    pool.query("SELECT preferred_roles, interests, industry_preferences FROM preferences WHERE user_id = $1", [userId]),
    pool.query("SELECT start_date, end_date FROM experience WHERE user_id = $1", [userId]),
  ]);
  const user = u.rows[0] || {};
  const resume = ri.rows[0] || {};
  let years = resume.years_of_experience ?? user.years_of_experience ?? null;
  if (years == null && exp.rows.length) {
    const total = exp.rows.reduce((acc, e) => {
      if (!e.start_date) return acc;
      const end = e.end_date ? new Date(e.end_date) : new Date();
      return acc + Math.max(0, (end - new Date(e.start_date)) / (365.25 * 864e5));
    }, 0);
    years = Math.round(total * 10) / 10;
  }
  const preferred = pref.rows[0] || {};
  return {
    user_id: userId,
    title: resume.professional_title || user.professional_title || user.career_goal_short || null,
    skills: [...new Set([...(resume.technical_skills || []), ...sk.rows.map((s) => s.skill_name)])],
    skill_proficiency: Object.fromEntries(sk.rows.map((s) => [s.skill_name, s.proficiency])),
    years_experience: years,
    location: user.location || null,
    work_mode: user.preferred_work_mode || null,
    preferred_roles: [...(preferred.preferred_roles || []), ...(user.career_goal_short ? [user.career_goal_short] : [])],
    interests: preferred.interests || [],
    industries: preferred.industry_preferences || [],
  };
}

// Strip fields the engine's Profile model does not accept.
function toEngineProfile(p) {
  const { skill_proficiency, interests, industries, ...rest } = p; // eslint-disable-line no-unused-vars
  return rest;
}

module.exports = { loadMatchProfile, toEngineProfile };
