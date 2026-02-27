package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {
    //Added comment for the main method
	public static void main(String[] args) {
		//added System.out which is non standard logging
		System.out.println("Test Main");
		SpringApplication.run(DemoApplication.class, args);
	}

}
