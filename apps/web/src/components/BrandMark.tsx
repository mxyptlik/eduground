export function BrandMark() {
  return (
    <div className="brand-mark" aria-label="EduGround Tutor">
      <span className="brand-mark__glyph" aria-hidden="true">
        <svg className="brand-mark__logo" viewBox="0 0 40 40" fill="none">
          <circle cx="20" cy="20" r="10.5" stroke="currentColor" strokeWidth="1.8" />
          <path
            d="M20 7.5v5.5M20 27v5.5M7.5 20h5.5M27 20h5.5"
            stroke="currentColor"
            strokeLinecap="round"
            strokeWidth="1.8"
          />
          <circle cx="20" cy="20" r="3.4" fill="currentColor" />
        </svg>
      </span>
      <div className="brand-mark__copy">
        <small className="brand-mark__eyebrow">Curriculum Atelier</small>
        <strong>EduGround</strong>
        <span>Tutor</span>
      </div>
    </div>
  );
}
