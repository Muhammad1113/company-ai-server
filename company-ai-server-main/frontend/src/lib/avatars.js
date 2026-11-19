// Naam se avatar: male naam => male tasveer, female naam => female tasveer.
// Tasveerein public/avatars/ mein hain. Ek hi jins ke kai avatar hon to username se ek chun liya jata hai
// (har dafa wahi), is liye alag alag log alag dikhte hain.
//
// Naam se andaza kabhi kabhi ghalat ho sakta hai (unisex naam, ya "admin" jaisa username).
// Kisi ka avatar pakka karna ho to neeche OVERRIDES mein likho: username (chhote harf) -> avatar ka naam.
// Naam jo is list mein na ho aur OVERRIDES mein bhi na ho, uski jagah initials wala gola dikhta hai.

const AVATARS = {
  male: ['male-1', 'male-2', 'male-3', 'male-4'],
  female: ['female-1', 'female-2'],
};

export const OVERRIDES = {
  admin: 'male-3',
};

const MALE = new Set(`
  qasim muhammad mohammad mohammed ahmed ahmad ali usman umar omar hassan hasan hussain husain bilal hamza talha zain zayn
  fahad faisal imran irfan kamran shahid saad saif sajid salman farhan junaid asad adnan arslan awais danish fawad haider
  hammad ibrahim ismail jawad kashif khalid nadeem naveed rehan rizwan shoaib tariq waqar waseem yasir zubair abdullah abdul
  ayaan ahsan akbar amir aamir anas arif atif babar basit daud dawood ehsan fahim faraz furqan ghulam hafiz haroon hashim
  huzaifa idrees ilyas jamal kabir kamal luqman mansoor mubashir mudassir mustafa nabeel nasir noman owais qaiser rafay
  raheel rashid raza sabir sameer shahzad shan sharjeel sohail sulaiman taimoor talal touseef umair uzair wahab waleed
  yousaf yousuf zaheer zeeshan zohaib zulfiqar daniel david john michael james robert william thomas peter paul mark
  steven kevin brian jason adam alex ryan sam noah liam ethan
`.split(/\s+/).filter(Boolean));

const FEMALE = new Set(`
  ayesha aisha ayeshah fatima fatimah zainab maryam mariam sana sara sarah hina nida iqra amna aliya alia bushra faiza farah
  huma kinza laiba maham mehwish nimra rabia rida saba sadia samina sidra sobia tahira uzma zara zoya anum areeba anaya
  emaan eman hira javeria komal mahnoor mahira mehreen mubashra nadia nayab rukhsar shumaila sumaira tooba urooj warda
  zunaira amina asma beenish dua duaa eshal fiza gul hafsa haleema hania humaira jannat khadija khadijah kiran lubna madiha
  maira mahrukh maria marium memona misbah mishal moomal neha nargis noreen pari parveen rimsha rubab sabahat saira
  samreen sanam sehrish shaista shazia sheeza shifa sumbul summaya syeda tabassum tayyaba umaima yumna zahra zubaida
  zehra emma olivia sophia anna emily jessica mary lisa sarah laura julia sophie
`.split(/\s+/).filter(Boolean));

function hash(text) {
  let h = 0;
  for (let i = 0; i < text.length; i += 1) h = (h * 31 + text.charCodeAt(i)) >>> 0;
  return h;
}

export function genderOf(username) {
  const tokens = String(username || '').toLowerCase().split(/[^a-z]+/).filter(Boolean);
  for (const t of tokens) {
    if (FEMALE.has(t)) return 'female';
    if (MALE.has(t)) return 'male';
  }
  return null;
}

/** { src, gender } ya null (null => initials wala gola). */
export function avatarFor(username) {
  const key = String(username || '').trim().toLowerCase();
  if (!key) return null;
  const forced = OVERRIDES[key];
  if (forced) {
    return { src: `/avatars/${forced}.jpg`, gender: forced.startsWith('female') ? 'female' : 'male' };
  }
  const gender = genderOf(key);
  if (!gender) return null;
  const list = AVATARS[gender];
  return { src: `/avatars/${list[hash(key) % list.length]}.jpg`, gender };
}
