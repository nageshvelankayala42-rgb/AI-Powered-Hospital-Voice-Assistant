CREATE DATABASE IF NOT EXISTS hospital_ai_booking;
USE hospital_ai_booking;

DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS doctors;

CREATE TABLE doctors (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    specialty VARCHAR(100) NOT NULL,
    qualification VARCHAR(100) NOT NULL,
    experience_years INT NOT NULL,
    available_days VARCHAR(150) NOT NULL,
    available_time VARCHAR(80) NOT NULL
);

CREATE TABLE appointments (
    id INT AUTO_INCREMENT PRIMARY KEY,
    patient_name VARCHAR(100) NOT NULL,
    patient_email VARCHAR(150) NOT NULL,
    patient_phone VARCHAR(20) NOT NULL,
    symptoms TEXT NOT NULL,
    appointment_date DATE NOT NULL,
    appointment_time VARCHAR(20) NOT NULL,
    doctor_id INT NOT NULL,
    status VARCHAR(30) NOT NULL DEFAULT 'Booked',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_appointments_doctor
        FOREIGN KEY (doctor_id) REFERENCES doctors(id)
        ON DELETE CASCADE
);

INSERT INTO doctors
    (name, specialty, qualification, experience_years, available_days, available_time)
VALUES
    ('Dr. Ananya Rao', 'Cardiology', 'MD, DM Cardiology', 12, 'Monday, Wednesday, Friday', '10:00 AM - 2:00 PM'),
    ('Dr. Vikram Mehta', 'Dermatology', 'MD Dermatology', 8, 'Tuesday, Thursday, Saturday', '11:00 AM - 4:00 PM'),
    ('Dr. Priya Nair', 'Neurology', 'DM Neurology', 10, 'Monday, Tuesday, Thursday', '9:00 AM - 1:00 PM'),
    ('Dr. Arjun Kapoor', 'Orthopedics', 'MS Orthopedics', 15, 'Wednesday, Friday, Saturday', '12:00 PM - 5:00 PM'),
    ('Dr. Sana Khan', 'General Medicine', 'MD Internal Medicine', 9, 'Monday to Saturday', '9:00 AM - 6:00 PM'),
    ('Dr. Rohan Iyer', 'Pediatrics', 'MD Pediatrics', 7, 'Monday, Wednesday, Saturday', '10:00 AM - 3:00 PM');
