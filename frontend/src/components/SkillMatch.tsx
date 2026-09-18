interface SkillMatchProps {
  recommendedCv: string | null;
  matchingSkills: string[];
  missingSkills: string[];
}

/**
 * "CV recomendado y explicación del match" (ROADMAP.md Fase 6 punto 2):
 * dos listas claramente separadas -- nunca un blob de texto -- reflejando
 * `matching_skills`/`missing_skills` calculados por el backend
 * (`app/presentation/api/routes/jobs.py::_compute_skill_match`). Este
 * componente no recalcula ni reinterpreta ese cruce, solo lo muestra.
 */
export function SkillMatch({ recommendedCv, matchingSkills, missingSkills }: SkillMatchProps) {
  if (!recommendedCv) {
    return <p>No CV recommended yet (CV Matcher has not run).</p>;
  }

  return (
    <div className="skill-match">
      <p>
        Recommended CV: <strong>{recommendedCv}</strong>
      </p>
      <div className="skill-match-columns">
        <div className="skill-column skill-column-match">
          <h3>Matching skills</h3>
          {matchingSkills.length > 0 ? (
            <ul>
              {matchingSkills.map((skill) => (
                <li key={skill}>{skill}</li>
              ))}
            </ul>
          ) : (
            <p>No matching skills.</p>
          )}
        </div>
        <div className="skill-column skill-column-missing">
          <h3>Missing skills</h3>
          {missingSkills.length > 0 ? (
            <ul>
              {missingSkills.map((skill) => (
                <li key={skill}>{skill}</li>
              ))}
            </ul>
          ) : (
            <p>No missing skills.</p>
          )}
        </div>
      </div>
    </div>
  );
}
