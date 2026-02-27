package com.example.demo;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class DemoApplication {

	//This is the main method of the spring boot microservice
	public static void main(String[] args) {
		//adding non standard logging
		System.out.println("Here is the non standard print");
		SpringApplication.run(DemoApplication.class, args);
	}

}
